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

from claim_worker import start_claim, submit_otp, resend_code


load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_RAW = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
ALLOWED_ID = int(ALLOWED_RAW) if ALLOWED_RAW.isdigit() else None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("xl-esim-bot")

ASK_NAME, ASK_EMAIL, ASK_WA, WAIT_OTP = range(4)


@dataclass
class Draft:
    name: str = ""
    email: str = ""
    whatsapp: str = ""
    session: Optional[object] = None


drafts = {}

# Maksimal klaim sukses per Telegram user selama proses bot aktif.
# Catatan: counter ini akan reset jika Railway restart/redeploy.
MAX_CLAIMS_PER_USER = 5
claim_counts = {}


def claims_used(uid: int) -> int:
    return claim_counts.get(uid, 0)


def claims_remaining(uid: int) -> int:
    return max(0, MAX_CLAIMS_PER_USER - claims_used(uid))


def authorized(update: Update) -> bool:
    if ALLOWED_ID is None:
        return True

    return (
        update.effective_user is not None
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


def otp_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Kirim Ulang Kode",
                callback_data="resend_code"
            )
        ],
        [
            InlineKeyboardButton(
                "🧹 Batalkan",
                callback_data="cancel"
            )
        ]
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
        "1. Masukkan Nama Lengkap\n"
        "2. Masukkan Email\n"
        "3. Masukkan Nomor WhatsApp\n"
        "4. Bot membuka halaman XL\n"
        "5. XL mengirim kode konfirmasi ke email\n"
        "6. Kirim kode 6 karakter ke bot\n"
        "7. Bot mengisi 1 karakter per kotak\n\n"
        "Contoh kode:\n"
        "<code>CKBILA</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=dashboard(),
    )


async def callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if not authorized(update):
        return ConversationHandler.END

    uid = update.effective_user.id
    action = query.data

    if action == "new":
        used = claims_used(uid)

        if used >= MAX_CLAIMS_PER_USER:
            await query.message.reply_text(
                "🚫 <b>Limit Klaim Tercapai</b>\n\n"
                f"Setiap akun Telegram maksimal <b>{MAX_CLAIMS_PER_USER}x klaim sukses</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=dashboard(),
            )
            return ConversationHandler.END

        old = drafts.pop(uid, None)

        if old and old.session:
            try:
                await old.session.close()
            except Exception:
                pass

        drafts[uid] = Draft()

        await query.message.reply_text(
            "👤 <b>Kirim Nama Lengkap</b>\n\n"
            f"Sisa klaim: <b>{claims_remaining(uid)}</b>",
            parse_mode=ParseMode.HTML,
        )

        return ASK_NAME

    if action == "status":
        draft = drafts.get(uid)

        if not draft:
            text = (
                "Tidak ada proses klaim aktif.\n\n"
                f"📊 Klaim sukses: {claims_used(uid)}/{MAX_CLAIMS_PER_USER}\n"
                f"Sisa klaim: {claims_remaining(uid)}"
            )

        elif (
            draft.session
            and draft.session.state == "WAITING_OTP"
        ):
            text = "📩 Status: menunggu kode konfirmasi."

        else:
            text = "⏳ Status: proses klaim sedang berjalan."

        await query.message.reply_text(
            text,
            reply_markup=dashboard(),
        )

        return ConversationHandler.END

    if action == "cancel":
        draft = drafts.pop(uid, None)

        if draft and draft.session:
            try:
                await draft.session.close()
            except Exception:
                pass

        await query.message.reply_text(
            "🧹 Proses klaim dibatalkan.",
            reply_markup=dashboard(),
        )

        return ConversationHandler.END

    if action == "help":
        await query.message.reply_text(
            "<b>Cara Menggunakan Bot</b>\n\n"
            "1. Tekan Klaim Baru\n"
            "2. Masukkan Nama Lengkap\n"
            "3. Masukkan Email\n"
            "4. Masukkan Nomor WhatsApp\n"
            "5. Tunggu kode dari XL\n"
            "6. Kirim kode konfirmasi 6 karakter\n\n"
            "Contoh:\n"
            "<code>CKBILA</code>\n"
            "<code>YKZVAT</code>\n"
            "<code>ABC123</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=dashboard(),
        )

        return ConversationHandler.END


async def resend_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    uid = update.effective_user.id
    draft = drafts.get(uid)

    if not draft or not draft.session:
        await query.message.reply_text(
            "❌ Sesi klaim tidak tersedia.",
            reply_markup=dashboard(),
        )
        return ConversationHandler.END

    try:
        result = await resend_code(
            draft.session
        )

        await query.message.reply_text(
            "🔄 " + result + "\n\n"
            "Kirim kode baru setelah masuk ke email.",
            reply_markup=otp_keyboard(),
        )

        return WAIT_OTP

    except Exception as exc:
        await query.message.reply_text(
            f"❌ {str(exc)}",
            reply_markup=otp_keyboard(),
        )

        return WAIT_OTP


async def got_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id
    name = update.effective_message.text.strip()

    if len(name) < 2:
        await update.effective_message.reply_text(
            "❌ Nama tidak valid."
        )
        return ASK_NAME

    drafts[uid].name = name

    await update.effective_message.reply_text(
        "📧 <b>Kirim Email</b>",
        parse_mode=ParseMode.HTML,
    )

    return ASK_EMAIL


async def got_email(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id
    email = update.effective_message.text.strip()

    if not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        email
    ):
        await update.effective_message.reply_text(
            "❌ Format email tidak valid."
        )
        return ASK_EMAIL

    drafts[uid].email = email

    await update.effective_message.reply_text(
        "📱 <b>Kirim Nomor WhatsApp</b>\n\n"
        "Contoh:\n"
        "<code>081234567890</code>",
        parse_mode=ParseMode.HTML,
    )

    return ASK_WA


async def got_whatsapp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id
    draft = drafts[uid]

    whatsapp = re.sub(
        r"[^\d+]",
        "",
        update.effective_message.text
    )

    if not re.fullmatch(
        r"(?:\+62|62|0)8\d{7,12}",
        whatsapp
    ):
        await update.effective_message.reply_text(
            "❌ Nomor WhatsApp tidak valid."
        )
        return ASK_WA

    draft.whatsapp = whatsapp

    await update.effective_message.reply_text(
        "⏳ Membuka halaman XL dan mengisi 3 field..."
    )

    try:
        draft.session = await start_claim(
            draft.name,
            draft.email,
            draft.whatsapp
        )

        await update.effective_message.reply_text(
            "📩 <b>Kode Konfirmasi</b>\n\n"
            f"Cek email:\n"
            f"<code>{draft.email}</code>\n\n"
            "Kirim kode konfirmasi "
            "<b>tepat 6 karakter</b>.\n\n"
            "Kode boleh berupa huruf atau angka.\n\n"
            "Contoh:\n"
            "<code>CKBILA</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=otp_keyboard(),
        )

        return WAIT_OTP

    except Exception as exc:
        log.exception("start_claim failed")

        drafts.pop(uid, None)

        await update.effective_message.reply_text(
            "❌ <b>Klaim belum bisa dilanjutkan</b>\n\n"
            f"<code>{str(exc)[:700]}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=dashboard(),
        )

        return ConversationHandler.END


async def got_otp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id
    draft = drafts.get(uid)

    if not draft or not draft.session:
        await update.effective_message.reply_text(
            "❌ Sesi klaim tidak ditemukan.",
            reply_markup=dashboard(),
        )
        return ConversationHandler.END

    code = re.sub(
        r"[^A-Za-z0-9]",
        "",
        update.effective_message.text
    ).upper()

    # IMPORTANT:
    # This is NOT numeric-only OTP.
    # Valid examples: CKBILA, YKZVAT, ABC123.
    if not re.fullmatch(
        r"[A-Z0-9]{6}",
        code
    ):
        await update.effective_message.reply_text(
            "❌ Kode konfirmasi harus tepat "
            "<b>6 karakter huruf/angka</b>.\n\n"
            "Contoh:\n"
            "<code>CKBILA</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=otp_keyboard(),
        )

        return WAIT_OTP

    await update.effective_message.reply_text(
        "🔐 Mengisi kode konfirmasi..."
    )

    try:
        result = await submit_otp(
            draft.session,
            code
        )

        if draft.session.state == "WAITING_OTP":
            await update.effective_message.reply_text(
                "❌ " + result + "\n\n"
                "Kirim kode terbaru atau tekan "
                "<b>Kirim Ulang Kode</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=otp_keyboard(),
            )

            return WAIT_OTP

        # Tambah counter hanya setelah proses klaim benar-benar sukses.
        claim_counts[uid] = claims_used(uid) + 1

        await update.effective_message.reply_text(
            "✅ " + result + "\n\n"
            f"📊 Klaim sukses: <b>{claims_used(uid)}/{MAX_CLAIMS_PER_USER}</b>\n"
            f"Sisa klaim: <b>{claims_remaining(uid)}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=dashboard(),
        )

        try:
            await draft.session.close()
        except Exception:
            pass

        drafts.pop(uid, None)

        return ConversationHandler.END

    except Exception as exc:
        log.exception("confirmation code failed")

        await update.effective_message.reply_text(
            "❌ <b>Kode gagal diproses</b>\n\n"
            f"<code>{str(exc)[:600]}</code>\n\n"
            "Kamu masih bisa kirim kode baru.",
            parse_mode=ParseMode.HTML,
            reply_markup=otp_keyboard(),
        )

        return WAIT_OTP


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id
    draft = drafts.pop(uid, None)

    if draft and draft.session:
        try:
            await draft.session.close()
        except Exception:
            pass

    await update.effective_message.reply_text(
        "🧹 Proses dibatalkan.",
        reply_markup=dashboard(),
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
                    got_whatsapp
                )
            ],

            WAIT_OTP: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    got_otp
                ),
                CallbackQueryHandler(
                    resend_callback,
                    pattern="^resend_code$"
                ),
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

    app.add_handler(
        conversation
    )

    print(
        "=== XL eSIM BOT - 6 CHARACTER ALPHANUMERIC CODE ==="
    )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
