# XL eSIM Telegram Assistant — Clean Build

Project baru dari nol untuk membantu satu proses klaim eSIM XL dengan OTP manual.

## Flow
`/start` → Klaim Baru → Nama → Email → WhatsApp → Playwright buka halaman XL → isi 3 field → klik Lanjut → tunggu halaman OTP → user kirim OTP 6 digit → bot isi OTP → lanjut.

## Setup
1. Copy `.env.example` ke `.env`
2. Isi `TELEGRAM_BOT_TOKEN`
3. Opsional isi `TELEGRAM_ALLOWED_USER_ID`
4. Jalankan:
   `pip install -r requirements.txt`
   `playwright install chromium`
   `python bot.py`

## Railway
Upload project ini sebagai project baru.
Set Environment Variables:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_ALLOWED_USER_ID
- CLAIM_URL=https://www.xl.co.id/esim-trial/claim
- HEADLESS=true
- BROWSER_TIMEOUT_MS=45000

Catatan:
- Tidak membypass CAPTCHA.
- Tidak mengakali eligibility.
- OTP dimasukkan manual oleh user.
