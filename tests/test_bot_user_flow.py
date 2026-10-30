import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ConversationHandler

from bot import handlers, keyboards
from services import identity_image_checks, verified_reporters


class UserPromptCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_back_button_restores_previous_step_without_losing_draft(self):
        message = SimpleNamespace(chat_id=42, message_id=8)
        query = SimpleNamespace(
            data="usr:back:description",
            from_user=SimpleNamespace(id=123),
            message=message,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(return_value=message),
        )
        context = SimpleNamespace(
            user_data={
                "report_state": handlers.ASK_PHOTO,
                "report_category": "conduct",
                "description": "Saved report description",
                handlers.ACTIVE_PROMPT_KEY: {
                    "chat_id": 42,
                    "message_id": 8,
                    "keyboard_kind": "inline",
                },
            },
            bot_data={"db": object()},
        )
        update = SimpleNamespace(callback_query=query)

        state = await handlers.on_user_callback(update, context)

        self.assertEqual(state, handlers.ASK_DESC)
        self.assertEqual(context.user_data["description"], "Saved report description")
        self.assertEqual(context.user_data["report_state"], handlers.ASK_DESC)
        self.assertEqual(
            query.edit_message_text.await_args.kwargs["reply_markup"]
            .inline_keyboard[0][0]
            .callback_data,
            "usr:back:category",
        )

    async def test_replaces_reply_keyboard_and_removes_previous_prompt(self):
        context = SimpleNamespace(
            user_data={
                handlers.ACTIVE_PROMPT_KEY: {
                    "chat_id": 42,
                    "message_id": 7,
                    "keyboard_kind": "reply",
                }
            },
            bot=SimpleNamespace(
                delete_message=AsyncMock(),
                send_message=AsyncMock(
                    return_value=SimpleNamespace(chat_id=42, message_id=8)
                ),
                edit_message_reply_markup=AsyncMock(),
            ),
        )
        message = SimpleNamespace(
            reply_text=AsyncMock(
                return_value=SimpleNamespace(chat_id=42, message_id=8)
            )
        )
        update = SimpleNamespace(callback_query=None, effective_message=message)
        markup = InlineKeyboardMarkup(
            [[keyboards._cb("Back", "usr:menu")]]
        )

        await handlers._send_clean_prompt(
            update, context, "Next step", reply_markup=markup
        )

        context.bot.delete_message.assert_awaited_once_with(
            chat_id=42, message_id=7
        )
        sent_markup = message.reply_text.await_args.kwargs["reply_markup"]
        self.assertIsInstance(sent_markup, ReplyKeyboardRemove)
        context.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=42, message_id=8, reply_markup=markup
        )
        self.assertEqual(
            context.user_data[handlers.ACTIVE_PROMPT_KEY]["message_id"], 8
        )
        self.assertEqual(
            context.user_data[handlers.ACTIVE_PROMPT_KEY]["keyboard_kind"],
            "inline",
        )

    async def test_failed_delete_removes_old_inline_keyboard(self):
        context = SimpleNamespace(
            user_data={
                handlers.ACTIVE_PROMPT_KEY: {
                    "chat_id": 42,
                    "message_id": 7,
                    "keyboard_kind": "inline",
                }
            },
            bot=SimpleNamespace(
                delete_message=AsyncMock(side_effect=handlers.TelegramError("denied")),
                edit_message_reply_markup=AsyncMock(),
            ),
        )

        await handlers._clear_user_prompt(context)

        context.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=42, message_id=7, reply_markup=None
        )
        self.assertNotIn(handlers.ACTIVE_PROMPT_KEY, context.user_data)

    async def test_main_menu_places_primary_actions_first(self):
        rows = keyboards.main_menu_kb().inline_keyboard

        self.assertEqual(rows[0][0].callback_data, "usr:report")
        self.assertEqual(rows[1][0].callback_data, "usr:status")
        self.assertEqual(rows[2][0].callback_data, "usr:about")
        self.assertEqual(rows[2][1].callback_data, "usr:contact")

    async def test_main_menu_shows_verification_when_encryption_is_ready(self):
        with patch.object(verified_reporters, "is_configured", return_value=True):
            rows = keyboards.main_menu_kb().inline_keyboard

        self.assertEqual(rows[1][0].callback_data, "usr:verify")
        self.assertEqual(rows[2][0].callback_data, "usr:verify-status")

    async def test_confirmation_keeps_final_receipt_navigation_separate(self):
        receipt = keyboards.saved_receipt_kb()

        self.assertEqual(
            receipt.inline_keyboard[0][0].callback_data, "usr:home"
        )
        self.assertIsInstance(keyboards.contact_request_kb(), ReplyKeyboardMarkup)
        self.assertIsInstance(keyboards.location_request_kb(), ReplyKeyboardMarkup)


class VerificationConversationTests(unittest.IsolatedAsyncioTestCase):
    def _make_update(self, mime_type="image/jpeg"):
        document = SimpleNamespace(
            file_id="synthetic-image",
            file_size=20,
            mime_type=mime_type,
        )
        reply_message = SimpleNamespace(chat_id=42, message_id=8)
        message = SimpleNamespace(
            document=document,
            reply_text=AsyncMock(return_value=reply_message),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(type="private"),
            callback_query=None,
        )
        telegram_file = SimpleNamespace(
            download_as_bytearray=AsyncMock(
                side_effect=lambda buffer: buffer.extend(b"synthetic-image-bytes")
            )
        )
        database = SimpleNamespace(
            extend_verification_ticket=AsyncMock(return_value=True),
            submit_verification_documents=AsyncMock(return_value=42),
            cancel_verification_capture=AsyncMock(return_value=True),
        )
        context = SimpleNamespace(
            user_data={
                handlers.VERIFICATION_TICKET_KEY: "ticket-hash",
                handlers.VERIFICATION_CAPTURES_KEY: {},
                handlers.VERIFICATION_STEP_KEY: "selfie",
            },
            bot_data={"db": database},
            bot=SimpleNamespace(get_file=AsyncMock(return_value=telegram_file)),
        )
        return update, context, database

    async def test_accepts_a_document_and_advances_to_the_next_step(self):
        update, context, database = self._make_update()
        with (
            patch.object(identity_image_checks, "validate_identity_image"),
            patch.object(
                verified_reporters,
                "encrypt_document",
                return_value=b"encrypted-selfie",
            ),
        ):
            state = await handlers.receive_verification_selfie(update, context)

        self.assertEqual(state, handlers.ASK_VERIFICATION_ID_FRONT)
        self.assertEqual(
            context.user_data[handlers.VERIFICATION_CAPTURES_KEY]["selfie"],
            b"encrypted-selfie",
        )
        database.extend_verification_ticket.assert_awaited_once()
        database.submit_verification_documents.assert_not_awaited()

    async def test_rejects_unclear_image_and_keeps_current_step(self):
        update, context, database = self._make_update()
        with (
            patch.object(
                identity_image_checks,
                "validate_identity_image",
                side_effect=identity_image_checks.IdentityImageCheckError(
                    "image_blurry"
                ),
            ),
            patch.object(verified_reporters, "encrypt_document") as encrypt,
        ):
            state = await handlers.receive_verification_selfie(update, context)

        self.assertEqual(state, handlers.ASK_VERIFICATION_SELFIE)
        self.assertEqual(
            context.user_data[handlers.VERIFICATION_CAPTURES_KEY], {}
        )
        encrypt.assert_not_called()
        database.extend_verification_ticket.assert_not_awaited()

    async def test_final_document_submits_encrypted_set_for_human_review(self):
        update, context, database = self._make_update()
        context.user_data[handlers.VERIFICATION_STEP_KEY] = "id_back"
        context.user_data[handlers.VERIFICATION_CAPTURES_KEY] = {
            "selfie": b"encrypted-selfie",
            "id_front": b"encrypted-front",
        }
        with (
            patch.object(identity_image_checks, "validate_identity_image"),
            patch.object(
                verified_reporters,
                "encrypt_document",
                return_value=b"encrypted-back",
            ),
        ):
            state = await handlers.receive_verification_id_back(update, context)

        self.assertEqual(state, ConversationHandler.END)
        database.submit_verification_documents.assert_awaited_once_with(
            123,
            "ticket-hash",
            {
                "selfie": b"encrypted-selfie",
                "id_front": b"encrypted-front",
                "id_back": b"encrypted-back",
            },
        )
        self.assertNotIn(handlers.VERIFICATION_CAPTURES_KEY, context.user_data)

    async def test_status_button_does_not_cancel_an_in_progress_capture(self):
        message = SimpleNamespace(chat_id=42, message_id=8)
        query = SimpleNamespace(
            data="usr:verify-status",
            from_user=SimpleNamespace(id=123),
            message=message,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(return_value=message),
        )
        database = SimpleNamespace(
            verification_status=AsyncMock(
                return_value={"status": "capturing", "has_documents": 0}
            ),
            cancel_verification_capture=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                handlers.VERIFICATION_TICKET_KEY: "ticket-hash",
                handlers.VERIFICATION_CAPTURES_KEY: {"selfie": b"encrypted"},
                handlers.VERIFICATION_STEP_KEY: "id_front",
            },
            bot_data={"db": database},
        )

        state = await handlers.on_user_callback(
            SimpleNamespace(callback_query=query), context
        )

        self.assertEqual(state, handlers.ASK_VERIFICATION_ID_FRONT)
        self.assertIn(handlers.VERIFICATION_TICKET_KEY, context.user_data)
        database.cancel_verification_capture.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
