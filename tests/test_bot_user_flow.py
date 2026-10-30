import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot import handlers, keyboards


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

    async def test_confirmation_keeps_final_receipt_navigation_separate(self):
        receipt = keyboards.saved_receipt_kb()

        self.assertEqual(
            receipt.inline_keyboard[0][0].callback_data, "usr:home"
        )
        self.assertIsInstance(keyboards.contact_request_kb(), ReplyKeyboardMarkup)
        self.assertIsInstance(keyboards.location_request_kb(), ReplyKeyboardMarkup)


if __name__ == "__main__":
    unittest.main()
