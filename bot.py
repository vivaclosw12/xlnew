import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from claim_worker import start_claim, submit_otp

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_RAW = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
ALLOWED_ID = int(ALLOWED_RAW) if ALLOWED_RAW.isdigit() else None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("xl-esim")

ASK_NAME, ASK_EMAIL, ASK_WA, WAIT_CODE = range(4)


@dataclass
class Draft:
    name: str = ""
    email: str = ""
    wa: str = ""
    session: Optional[object] = None


drafts = {}


def authorized(update: Update) -> bool:
    return ALLOWED_ID is None or (
        update.effective_user
        and update.effective_user.id == ALLOWED_ID
    )


def dashboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚡ Klaim Baru",
                callback_data="new"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Status",
                callback_data="status"
            ),
            InlineKeyboardButton(
                "🧹 Batalkan",
                callback_data="cancel"
            ),
        ],
        [
            InlineKeyboardButton(
                "ℹ️ Bantuan",
                callback_data="help"
            )
        ],
    ])


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not authorized(update):
        await update.effective_message.reply_text(
            "Akses bot dibatasi."
        )
        return

    await update.effective_message.reply_text(
        "<b>XL eSIM Claim Assistant</b>\n\n"
        "Dashboard klaim pribadi.\n\n"
        "1. Masukkan data\n"
        "2. Bot mengisi halaman XL\n"
        "3. Kode konfirmasi kamu masukkan manual\n"
        "4. Bot melanjutkan proses\n\n"
        "Tidak ada bypass CAPTCHA atau eligibility.",
        parse_mode=ParseMode.HTML,
        reply_markup=dashboard(),
    )


async def callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    q = update.callback_query
    await q.answer()

    if not authorized(update):
        return ConversationHandler.END

    uid = update.effective_user.id

    if q.data == "new":
        old = drafts.pop(uid, None)

        if old and old.session:
            await old.session.close()

        drafts[uid] = Draft()

        await q.message.reply_text(
            "Kirim Nama Lengkap."
        )

        return ASK_NAME

    if q.data == "status":
        d = drafts.get(uid)

        if not d:
            text = "Tidak ada proses klaim aktif."

        elif (
            d.session
            and d.session.state == "WAITING_OTP"
        ):
            text = "Status: menunggu kode konfirmasi."

        else:
            text = "Status: sedang mengisi data."

        await q.message.reply_text(
            text,
            reply_markup=dashboard()
        )

        return ConversationHandler.END

    if q.data == "cancel":
        d = drafts.pop(uid, None)

        if d and d.session:
            await d.session.close()

        await q.message.reply_text(
            "Proses dibatalkan.",
            reply_markup=dashboard()
        )

        return ConversationHandler.END

    if q.data == "help":
        await q.message.reply_text(
            "Klaim Baru → Nama → Email → WhatsApp "
            "→ tunggu kode → kirim kode konfirmasi.",
            reply_markup=dashboard(),
        )

        return ConversationHandler.END


async def got_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id

    value = update.effective_message.text.strip()

    if len(value) < 2:
        await update.effective_message.reply_text(
            "Nama tidak valid."
        )
        return ASK_NAME

    drafts[uid].name = value

    await update.effective_message.reply_text(
        "Kirim Email."
    )

    return ASK_EMAIL


async def got_email(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id

    value = update.effective_message.text.strip()

    if not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        value
    ):
        await update.effective_message.reply_text(
            "Format email tidak valid."
        )

        return ASK_EMAIL

    drafts[uid].email = value

    await update.effective_message.reply_text(
        "Kirim Nomor WhatsApp.\n"
        "Contoh: 081234567890"
    )

    return ASK_WA


async def got_wa(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id
    d = drafts[uid]

    value = re.sub(
        r"[^\d+]",
        "",
        update.effective_message.text
    )

    if not re.fullmatch(
        r"(?:\+62|62|0)8\d{7,12}",
        value
    ):
        await update.effective_message.reply_text(
            "Nomor WhatsApp tidak valid."
        )

        return ASK_WA

    d.wa = value

    await update.effective_message.reply_text(
        "⏳ Membuka halaman XL dan mengisi data..."
    )

    try:
        d.session = await start_claim(
            d.name,
            d.email,
            d.wa
        )

        await update.effective_message.reply_text(
            "📩 <b>Kode Konfirmasi</b>\n\n"
            f"Cek email <code>{d.email}</code>\n\n"
            "Kirim kode konfirmasi di sini.\n"
            "Kode boleh berupa huruf dan angka.",
            parse_mode=ParseMode.HTML,
        )

        return WAIT_CODE

    except Exception as exc:
        log.exception("claim failed")

        drafts.pop(uid, None)

        await update.effective_message.reply_text(
            "❌ Klaim belum bisa dilanjutkan.\n\n"
            f"<code>{str(exc)[:700]}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=dashboard(),
        )

        return ConversationHandler.END


async def got_code(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id
    d = drafts.get(uid)

    if not d or not d.session:
        await update.effective_message.reply_text(
            "Sesi klaim tidak ditemukan.",
            reply_markup=dashboard()
        )

        return ConversationHandler.END

    code = re.sub(
        r"[^A-Za-z0-9]",
        "",
        update.effective_message.text
    ).upper()

    # XL code can be alphabetic/alphanumeric, e.g. SJALI
    if not re.fullmatch(
        r"[A-Z0-9]{4,8}",
        code
    ):
        await update.effective_message.reply_text(
            "Kode harus 4–8 karakter huruf/angka."
        )

        return WAIT_CODE

    await update.effective_message.reply_text(
        "🔐 Mengirim kode konfirmasi..."
    )

    try:
        result = await submit_otp(
            d.session,
            code
        )

        if d.session.state == "WAITING_OTP":
            await update.effective_message.reply_text(
                "❌ " + result
            )

            return WAIT_CODE

        await update.effective_message.reply_text(
            "✅ " + result,
            reply_markup=dashboard(),
        )

        await d.session.close()

        drafts.pop(uid, None)

        return ConversationHandler.END

    except Exception as exc:
        log.exception("confirmation code failed")

        await update.effective_message.reply_text(
            "❌ Kode gagal diproses.\n\n"
            f"<code>{str(exc)[:600]}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=dashboard(),
        )

        try:
            await d.session.close()
        except Exception:
            pass

        drafts.pop(uid, None)

        return ConversationHandler.END


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id

    d = drafts.pop(uid, None)

    if d and d.session:
        await d.session.close()

    await update.effective_message.reply_text(
        "Proses dibatalkan.",
        reply_markup=dashboard()
    )

    return ConversationHandler.END


def main():

    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN belum diisi."
        )

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                callback,
                pattern="^(new|status|cancel|help)$"
            )
        ],

        states={
            ASK_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    got_name
                )
            ],

            ASK_EMAIL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    got_email
                )
            ],

            ASK_WA: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    got_wa
                )
            ],

            WAIT_CODE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    got_code
                )
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel
            )
        ],

        allow_reentry=True,
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "dashboard",
            start
        )
    )

    app.add_handler(conversation)

    print("XL eSIM bot running...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
