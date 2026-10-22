"""فحص واجهة Gradio 6 لتركيب تطبيق الويب (FastAPI) مع Gradio."""

import inspect

import gradio as gr

print("gradio", gr.__version__)
print("\n--- دوال التركيب المتاحة ---")
for name in dir(gr):
    if "mount" in name.lower() or name in {"App"}:
        obj = getattr(gr, name)
        try:
            sig = inspect.signature(obj)
        except Exception:
            sig = "(?)"
        print(f"  gr.{name}{sig}")

print("\n--- gr.routes ---")
try:
    from gradio.routes import App

    print("  App.create_app:", inspect.signature(App.create_app))
except Exception as exc:
    print("  لا يوجد App:", exc)

print("\n--- Blocks.launch ---")
print(" ", inspect.signature(gr.Blocks.launch))
