"""
YouTube OAuth Authentication Helper for AutoShorts Studio
This script helps you authenticate your YouTube channel and saves your access token.
"""
import os
import sys

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure AutoShorts Studio is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import config
from app.services.youtube_uploader import youtube_uploader_service


def main():
    print("=" * 60)
    print("AutoShorts Studio - YouTube Kanal Yetkilendirme Araci")
    print("=" * 60)

    client_secrets = youtube_uploader_service.client_secrets_file
    token_file = youtube_uploader_service.credentials_file

    print(f"\n[1] Istemci Yapilandirma Dosyasi: {client_secrets}")
    print(f"[2] Kaydedilecek Token Dosyasi : {token_file}\n")

    if not os.path.exists(client_secrets):
        alt_secret = os.path.join(config.root_dir, "client_secret.json")
        if os.path.exists(alt_secret):
            client_secrets = alt_secret
        else:
            print("[!] 'client_secret.json' dosyasi bulunamadi!")
            print("\nNasil Alinir:")
            print("1. Google Cloud Console'a gidin: https://console.cloud.google.com/")
            print("2. Yeni bir proje olusturun ve 'YouTube Data API v3' servisini etkinlestirin.")
            print("3. 'Credentials' (Kimlik Bilgileri) -> 'Create Credentials' -> 'OAuth client ID' secin.")
            print("4. Uygulama turu olarak 'Desktop app' (Masaustu uygulamasi) secin.")
            print("5. Indirilen JSON dosyasini su konuma kopyalayin:")
            print(f"   -> {client_secrets}")
            print("\nDosyayi kopyaladiktan sonra bu scripti tekrar calistirin.")
            if sys.stdin.isatty():
                input("\nCikmak icin Enter'a basin...")
            return 1

    print("[*] Tarayiciniz aciliyor, lutfen YouTube kanalinizin bagli oldugu hesapla giris yapin ve izin verin...")
    success = youtube_uploader_service.authenticate_via_browser()

    if success:
        print("\n[OK] TEBRIKLER! YouTube yetkilendirmesi basariyla tamamlandi.")
        print(f"Token kaydedildi: {token_file}")
        print("\nArtik uretilen videolar otomatik olarak kanaliniza Taslak (Private) Shorts olarak yuklenecektir.")
        print("Otomatik yuklemeyi acmak icin config.toml dosyasinda:")
        print("  youtube_enabled = true")
        print("  youtube_auto_upload = true")
        print("yapmaniz yeterlidir.")
    else:
        print("\n[X] Yetkilendirme basarisiz oldu. Lutfen konsol loglarini kontrol edin.")

    if sys.stdin.isatty():
        input("\nCikmak icin Enter'a basin...")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
