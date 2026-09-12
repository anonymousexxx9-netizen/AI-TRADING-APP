"""
main.py — Entry point. Menjalankan bot Telegram + bot Discord + scheduler
alert terpusat, semuanya dalam SATU proses/event loop.

Jalankan dengan:  python main.py
"""

import asyncio

# dotenv opsional — dipakai saat development lokal (.env file).
# Di Replit, env var sudah di-inject langsung oleh runtime.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import core
import telegram_bot
import discord_bot
import scheduler


async def run_telegram(app):
    """Start Telegram Application secara manual (bukan run_polling blocking)
    supaya bisa hidup berdampingan dengan Discord di event loop yang sama."""
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("✅ Telegram bot berjalan...")
    # tetap hidup selama proses berjalan
    await asyncio.Event().wait()


async def run_discord(bot):
    print("✅ Discord bot menghubungkan...")
    await bot.start(discord_bot.DISCORD_TOKEN)


async def main():
    core.init_db()

    telegram_app = telegram_bot.build_application()
    discord_client = discord_bot.build_bot()

    await asyncio.gather(
        run_telegram(telegram_app),
        run_discord(discord_client),
        scheduler.run_scheduler(telegram_app, discord_client),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot dihentikan.")
