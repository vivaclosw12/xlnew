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


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

ALLOWED_RAW = os.getenv(
    "TELEGRAM_ALLOWED_USER_ID",
    ""
).strip()

ALLOWED_ID = (
    int(ALLOWED_RAW)
    if ALLOWED_RAW.isdigit()
    else None
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("xl-esim-bot")


# ============================================================
# CONVERSATION STATES
# ============================================================

ASK_NAME, ASK_EMAIL, ASK_WA, WAIT_OTP = range(4)


# ============================================================
# SESSION DATA
# ============================================================

@dataclass
class Draft:
    name: str = ""
    email: str = ""
    whatsapp: str = ""
    session: Optional[object] = None


drafts = {}


# ============================================================
# AUTH
# ============================================================

def authorized(update: Update) -> bool:

    if ALLOWED_ID is None:
        return True

    return (
        update.effective_user is not None
        and update.effective_user.id == ALLOWED_ID
    )


# ============================================================
# DASHBOARD
# ============================================================

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


# ============================================================
# /START
# ============================================================

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

        "Alur klaim:\n\n"

        "1. Masukkan Nama Lengkap\n"
        "2. Masukkan Email\n"
        "3. Masukkan Nomor WhatsApp\n"
        "4. Bot membuka halaman XL\n"
        "5. XL mengirim kode konfirmasi ke email\n"
        "6. Kirim kode 6 karakter ke bot\n"
        "7. Bot mengisi kode ke halaman konfirmasi\n\n"

        "Contoh kode:\n"
        "<code>YKZVAT</code>",

        parse_mode=ParseMode.HTML,
        reply_markup=dashboard(),
    )


# ============================================================
# CALLBACK BUTTON
# ============================================================

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


    # --------------------------------------------------------
    # NEW CLAIM
    # --------------------------------------------------------

    if action == "new":

        old = drafts.pop(
            uid,
            None
        )


        if old and old.session:

            try:

                await old.session.close()

            except Exception:

                pass


        drafts[uid] = Draft()


        await query.message.reply_text(

            "👤 <b>Kirim Nama Lengkap</b>",

            parse_mode=ParseMode.HTML,
        )


        return ASK_NAME


    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if action == "status":

        draft = drafts.get(uid)


        if not draft:

            text = (
                "Tidak ada proses klaim aktif."
            )


        elif (
            draft.session
            and draft.session.state == "WAITING_OTP"
        ):

            text = (
                "📩 Status: menunggu kode konfirmasi."
            )


        else:

            text = (
                "⏳ Status: proses klaim sedang berjalan."
            )


        await query.message.reply_text(

            text,

            reply_markup=dashboard(),
        )


        return ConversationHandler.END


    # --------------------------------------------------------
    # CANCEL
    # --------------------------------------------------------

    if action == "cancel":

        draft = drafts.pop(
            uid,
            None
        )


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


    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------

    if action == "help":

        await query.message.reply_text(

            "<b>Cara Menggunakan Bot</b>\n\n"

            "1. Tekan Klaim Baru\n"
            "2. Masukkan Nama Lengkap\n"
            "3. Masukkan Email\n"
            "4. Masukkan Nomor WhatsApp\n"
            "5. Tunggu kode dari XL\n"
            "6. Kirim kode konfirmasi ke bot\n\n"

            "Kode terdiri dari tepat "
            "<b>6 karakter</b>.\n\n"

            "Contoh:\n"
            "<code>YKZVAT</code>\n"
            "<code>KLPYDI</code>\n"
            "<code>ABC123</code>",

            parse_mode=ParseMode.HTML,

            reply_markup=dashboard(),
        )


        return ConversationHandler.END


# ============================================================
# NAME
# ============================================================

async def got_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    uid = update.effective_user.id


    name = (
        update.effective_message.text
        .strip()
    )


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


# ============================================================
# EMAIL
# ============================================================

async def got_email(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    uid = update.effective_user.id


    email = (
        update.effective_message.text
        .strip()
    )


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


# ============================================================
# WHATSAPP
# ============================================================

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

            "Kemudian kirim kode konfirmasi "
            "<b>6 karakter</b> di sini.\n\n"

            "Kode dapat berupa huruf/angka.\n\n"

            "Contoh:\n"

            "<code>YKZVAT</code>",

            parse_mode=ParseMode.HTML,
        )


        return WAIT_OTP


    except Exception as exc:

        log.exception(
            "start_claim failed"
        )


        drafts.pop(
            uid,
            None
        )


        await update.effective_message.reply_text(

            "❌ <b>Klaim belum bisa dilanjutkan</b>\n\n"

            f"<code>{str(exc)[:700]}</code>",

            parse_mode=ParseMode.HTML,

            reply_markup=dashboard(),
        )


        return ConversationHandler.END


# ============================================================
# CONFIRMATION CODE
# ============================================================

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


    # ========================================================
    # IMPORTANT:
    # XL CODE = EXACTLY 6 ALPHANUMERIC CHARACTERS
    #
    # Example:
    # YKZVAT
    #
    # DO NOT REMOVE LETTERS.
    # ========================================================

    raw_code = (
        update.effective_message.text
        .strip()
    )


    code = re.sub(

        r"[^A-Za-z0-9]",

        "",

        raw_code

    ).upper()


    # ========================================================
    # VALIDATION
    #
    # VALID:
    #
    # YKZVAT
    # KLPYDI
    # ABC123
    # 123456
    #
    # INVALID:
    #
    # YKZVA
    # YKZVAT7
    # ========================================================

    if not re.fullmatch(

        r"[A-Z0-9]{6}",

        code

    ):

        await update.effective_message.reply_text(

            "❌ Kode konfirmasi harus tepat "
            "<b>6 karakter</b>.\n\n"

            "Huruf dan angka diperbolehkan.\n\n"

            "Contoh:\n"

            "<code>YKZVAT</code>",

            parse_mode=ParseMode.HTML,
        )


        return WAIT_OTP


    log.info(
        "Confirmation code accepted: length=%s",
        len(code)
    )


    await update.effective_message.reply_text(

        "🔐 Mengisi kode konfirmasi..."
    )


    try:

        # IMPORTANT:
        #
        # code is passed WHOLE:
        #
        # "YKZVAT"
        #
        # claim_worker.py is responsible for:
        #
        # Y -> box 1
        # K -> box 2
        # Z -> box 3
        # V -> box 4
        # A -> box 5
        # T -> box 6

        result = await submit_otp(

            draft.session,

            code
        )


        if (
            draft.session.state
            == "WAITING_OTP"
        ):

            await update.effective_message.reply_text(

                "❌ " + result
            )


            return WAIT_OTP


        await update.effective_message.reply_text(

            "✅ " + result,

            reply_markup=dashboard(),
        )


        try:

            await draft.session.close()

        except Exception:

            pass


        drafts.pop(
            uid,
            None
        )


        return ConversationHandler.END


    except Exception as exc:

        log.exception(
            "confirmation code failed"
        )


        await update.effective_message.reply_text(

            "❌ <b>Kode gagal diproses</b>\n\n"

            f"<code>{str(exc)[:600]}</code>",

            parse_mode=ParseMode.HTML,
        )


        return WAIT_OTP


# ============================================================
# CANCEL COMMAND
# ============================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    uid = update.effective_user.id


    draft = drafts.pop(
        uid,
        None
    )


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


# ============================================================
# MAIN
# ============================================================

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

                    filters.TEXT
                    & ~filters.COMMAND,

                    got_name
                )

            ],


            ASK_EMAIL: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    got_email
                )

            ],


            ASK_WA: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    got_whatsapp
                )

            ],


            WAIT_OTP: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    got_otp
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


    app.add_handler(

        conversation
    )


    print(
        "=== XL eSIM BOT - 6 CHARACTER CONFIRMATION CODE ==="
    )


    app.run_polling(

        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":

    main()
