import ast
import os
os.environ["HF_HUB_DISABLE_SSL_VERIFICATION"] = "1"

import warnings
warnings.filterwarnings('ignore')

import requests
original_request = requests.Session.request

def patched_request(*args, **kwargs):
    kwargs['verify'] = False
    return original_request(*args, **kwargs)

requests.Session.request = patched_request

import json
import time
import csv
import re
from urllib.parse import quote_plus

from datasets import load_dataset


# ============================================================
# KEYWORDS
# ============================================================

def load_keywords(config_path):
    print(f"Loading keywords from {config_path}...")

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

            if isinstance(config, list):
                return [kw.lower() for kw in config]

            elif isinstance(config, dict):
                return [
                    kw.lower()
                    for kw in config.get('queries', [])
                ]

            return []

    except Exception as e:
        print(f"Error loading config: {e}")
        return []


# ============================================================
# URL BULMA
# ============================================================

def extract_url(meta, text):
    """
    URL bulma sırası:

    1. meta["url"]
    2. meta["arxiv_id"]
    3. text içindeki arXiv URL
    4. text içindeki arXiv ID
    5. title üzerinden arXiv arama URL
    """

    if not isinstance(meta, dict):
        meta = {}

    # --------------------------------------------------------
    # 1. Direkt URL
    # --------------------------------------------------------

    url = meta.get("url")

    if not url:
        arxiv_id = meta.get("arxiv_id")

        if arxiv_id:
            url = "https://arxiv.org/abs/" + str(arxiv_id)

    if not url:
        url = "Unknown_URL"

    # --------------------------------------------------------
    # 2. ArXiv ID
    # --------------------------------------------------------

    arxiv_id = meta.get("arxiv_id")

    if isinstance(arxiv_id, str):
        arxiv_id = arxiv_id.strip()

        if arxiv_id:

            if arxiv_id.lower().startswith("arxiv:"):
                arxiv_id = arxiv_id.split(":", 1)[1].strip()

            return f"https://arxiv.org/abs/{arxiv_id}"

    # --------------------------------------------------------
    # 3. Text içinde arXiv URL
    # --------------------------------------------------------

    if isinstance(text, str):

        match = re.search(
            r'https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/[^\s<>"\'}]+',
            text,
            re.IGNORECASE
        )

        if match:

            url = match.group(0)

            url = url.rstrip(
                ".,;:)]}>\"'"
            )

            # PDF linkini abstract linkine çevir
            url = re.sub(
                r'/pdf/',
                '/abs/',
                url,
                flags=re.IGNORECASE
            )

            return url

    # --------------------------------------------------------
    # 4. Text içinde arXiv ID
    # --------------------------------------------------------

    if isinstance(text, str):

        match = re.search(
            r'arXiv\s*:\s*'
            r'([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)',
            text,
            re.IGNORECASE
        )

        if match:

            arxiv_id = match.group(1)

            return (
                f"https://arxiv.org/abs/{arxiv_id}"
            )

    # --------------------------------------------------------
    # 5. Title bul ve arXiv SEARCH linki oluştur
    # --------------------------------------------------------

    title = None

    # LaTeX \title{...}
    if isinstance(text, str):

        match = re.search(
            r'\\title\s*\{(.{5,500}?)\}',
            text,
            re.IGNORECASE | re.DOTALL
        )

        if match:

            title = match.group(1)

            title = re.sub(
                r'\\[a-zA-Z]+\s*',
                ' ',
                title
            )

            title = title.replace(
                "{", ""
            ).replace(
                "}", ""
            )

            title = re.sub(
                r'\s+',
                ' ',
                title
            ).strip()

    # Meta title
    if not title:

        for key in [
            "title",
            "paper_title"
        ]:

            value = meta.get(key)

            if isinstance(value, str):
                value = value.strip()

                if value:
                    title = value
                    break

    # Başlıktan arXiv arama linki
    if title:

        title = re.sub(
            r'\s+',
            ' ',
            title
        ).strip()

        title = title[:300]

        return (
            "https://arxiv.org/search/?query="
            + quote_plus(title)
            + "&searchtype=all"
        )

    # --------------------------------------------------------
    # Son çare
    # --------------------------------------------------------

    return "https://arxiv.org/search/"


# ============================================================
# INTERNET KONTROL
# ============================================================

def internet_available():

    try:
        requests.get(
            "https://huggingface.co",
            timeout=5,
            verify=False
        )

        return True

    except Exception:
        return False


# ============================================================
# INTERNET GELENE KADAR BEKLE
# ============================================================

def wait_for_internet():

    print()
    print("==================================================")
    print("[!] INTERNET BAĞLANTISI KESİLDİ")
    print("[!] Program kapanmayacak.")
    print("[!] Bağlantı geri gelene kadar bekleniyor.")
    print("==================================================")

    wait_time = 5

    while True:

        if internet_available():

            print()
            print("[OK] Internet bağlantısı geri geldi!")
            print()

            return

        print(
            f"[!] Bağlantı yok. "
            f"{wait_time} saniye sonra tekrar denenecek."
        )

        time.sleep(wait_time)

        # 5 -> 10 -> 20 -> 30 -> 30...
        wait_time = min(
            wait_time * 2,
            30
        )


# ============================================================
# ANA TARAMA
# ============================================================

def stream_and_filter(
    target_gb=10,
    chunk_gb=4.0,
    output_prefix="radar_massive_corpus",
    state_file="radar_stream_state.json"
):

    script_dir = os.path.dirname(
        os.path.abspath(__file__)
    )

    config_path = os.path.join(
        script_dir,
        "config.json"
    )

    keywords = load_keywords(
        config_path
    )

    if not keywords:
        print(
            "No keywords found in config.json. Exiting."
        )
        return

    print(
        f"Loaded {len(keywords)} radar keywords. "
        f"Commencing stream..."
    )

    target_bytes = (
        target_gb *
        1024 *
        1024 *
        1024
    )

    chunk_bytes = (
        chunk_gb *
        1024 *
        1024 *
        1024
    )

    # ========================================================
    # STATE
    # ========================================================

    processed_count = 0
    matched_count = 0

    state_path = os.path.join(
        script_dir,
        state_file
    )

    if os.path.exists(state_path):

        try:

            with open(
                state_path,
                'r',
                encoding='utf-8'
            ) as sf:

                state = json.load(sf)

            processed_count = state.get(
                "processed_count",
                0
            )

            matched_count = state.get(
                "matched_count",
                0
            )

            print(
                f"Resuming from existing state: "
                f"Processed {processed_count} documents."
            )

        except Exception as e:

            print(
                f"Warning: Could not read state file: {e}"
            )

    # ========================================================
    # CHUNK
    # ========================================================

    total_bytes = 0
    current_chunk = 1

    while os.path.exists(
        os.path.join(
            script_dir,
            f"{output_prefix}_part{current_chunk}.jsonl"
        )
    ):

        size = os.path.getsize(
            os.path.join(
                script_dir,
                f"{output_prefix}_part{current_chunk}.jsonl"
            )
        )

        if size < chunk_bytes:
            break

        total_bytes += size
        current_chunk += 1

    current_file_path = os.path.join(
        script_dir,
        f"{output_prefix}_part{current_chunk}.jsonl"
    )

    current_chunk_bytes = 0

    if os.path.exists(current_file_path):

        current_chunk_bytes = os.path.getsize(
            current_file_path
        )

        total_bytes += current_chunk_bytes

    print(
        f"Current total collected: "
        f"{total_bytes / (1024**3):.4f} GB"
    )

    print(
        f"Writing text to: "
        f"{output_prefix}_part{current_chunk}.jsonl"
    )

    # ========================================================
    # CSV
    # ========================================================

    csv_file_path = os.path.join(
        script_dir,
        "radar_sources_index.csv"
    )

    csv_exists = os.path.exists(
        csv_file_path
    )

    csv_f = open(
        csv_file_path,
        'a',
        encoding='utf-8',
        newline=''
    )

    csv_writer = csv.writer(
        csv_f
    )

    if not csv_exists:

        csv_writer.writerow([
            "Matched_Keywords",
            "ArXiv_URL",
            "Timestamp",
            "Snippet"
        ])

        print(
            "Created new tracking CSV: "
            "radar_sources_index.csv"
        )

    # ========================================================
    # DATASET OLUŞTUR
    # ========================================================

    while True:

        try:

            dataset = load_dataset(
                "togethercomputer/RedPajama-Data-1T",
                "arxiv",
                split="train",
                streaming=True,
                trust_remote_code=True
            )

            if processed_count > 0:

                print(
                    "Fast-forwarding stream to the "
                    "last saved position..."
                )

                dataset = dataset.skip(
                    processed_count
                )

            break

        except Exception as e:

            print()
            print(
                f"[!] Dataset bağlantı hatası: {e}"
            )

            wait_for_internet()

    # ========================================================
    # TARAMA
    # ========================================================

    start_time = time.time()

    f = open(
        current_file_path,
        'a',
        encoding='utf-8'
    )

    try:

        while True:

            try:

                for item in dataset:

                    text = item.get(
                        "text",
                        ""
                    )

                    if not text:
                        continue

                    processed_count += 1

                    text_lower = text.lower()

                    # KURESEL RADAR SARTI (GLOBAL CONDITION)
                    # Eger 40 sayfalik makalenin icinde 1 kere bile 'radar' kelimesi gecmiyorsa
                    # bu makale %100 baska bir alana (Kamera, Tip, Astronomi) aittir.
                    # Anahtar kelimeleri hic aratmadan makaleyi direkt atla (Cok buyuk hiz ve guvenlik kazandirir)
                    if "radar" not in text_lower:
                        continue

                    # ANA KODUNDAKİ AYNI HIZLI KONTROL
                    matched_kws = [
                        kw
                        for kw in keywords
                        if kw in text_lower
                    ]

                    if matched_kws:

                        meta = item.get(
                            "meta",
                            {}
                        )

                        if isinstance(meta, str):
                            try:
                                meta = json.loads(meta)
                            except:
                                try:
                                    meta = ast.literal_eval(meta)
                                except:
                                    meta = {}

                        # ----------------------------------------
                        # VERİ
                        # ----------------------------------------

                        json_record = json.dumps(
                            {
                                "text": text,
                                "meta": meta
                            },
                            ensure_ascii=False
                        )

                        record_str = (
                            json_record +
                            "\n"
                        )

                        record_bytes = len(
                            record_str.encode(
                                'utf-8'
                            )
                        )

                        # ----------------------------------------
                        # CHUNK
                        # ----------------------------------------

                        if (
                            current_chunk_bytes +
                            record_bytes
                            > chunk_bytes
                        ):

                            f.flush()
                            f.close()

                            current_chunk += 1

                            current_file_path = os.path.join(
                                script_dir,
                                f"{output_prefix}_part{current_chunk}.jsonl"
                            )

                            print(
                                f"Chunk limit "
                                f"({chunk_gb} GB) reached. "
                                f"Opening new chunk: "
                                f"{current_file_path}"
                            )

                            f = open(
                                current_file_path,
                                'a',
                                encoding='utf-8'
                            )

                            current_chunk_bytes = 0

                        # ----------------------------------------
                        # JSONL KAYDET
                        # ----------------------------------------

                        f.write(
                            record_str
                        )

                        # ----------------------------------------
                        # URL
                        # ----------------------------------------

                        url = extract_url(
                            meta,
                            text
                        )

                        timestamp = meta.get(
                            "timestamp",
                            "Unknown_Time"
                        )

                        snippet = (
                            text[:80]
                            .replace("\n", " ")
                            .strip()
                            + "..."
                        )

                        # ----------------------------------------
                        # CSV
                        # ----------------------------------------

                        csv_writer.writerow([
                            ", ".join(
                                matched_kws
                            ),
                            url,
                            timestamp,
                            snippet
                        ])

                        matched_count += 1

                        current_chunk_bytes += (
                            record_bytes
                        )

                        total_bytes += (
                            record_bytes
                        )

                    # ====================================================
                    # CHECKPOINT
                    # ====================================================

                    if processed_count % 1000 == 0:

                        elapsed = (
                            time.time() -
                            start_time
                        )

                        gb_collected = (
                            total_bytes /
                            (1024**3)
                        )

                        print(
                            f"[{elapsed:.1f}s] "
                            f"Processed: {processed_count} | "
                            f"Matched: {matched_count} | "
                            f"Size: {gb_collected:.4f} GB / "
                            f"{target_gb} GB"
                        )

                        f.flush()
                        csv_f.flush()

                        with open(
                            state_path,
                            'w',
                            encoding='utf-8'
                        ) as sf:

                            json.dump(
                                {
                                    "processed_count":
                                        processed_count,

                                    "matched_count":
                                        matched_count
                                },
                                sf
                            )

                    # ====================================================
                    # HEDEF
                    # ====================================================

                    if total_bytes >= target_bytes:

                        print(
                            f"\nSUCCESS: "
                            f"Reached target dataset size "
                            f"of {target_gb} GB!"
                        )

                        with open(
                            state_path,
                            'w',
                            encoding='utf-8'
                        ) as sf:

                            json.dump(
                                {
                                    "processed_count":
                                        processed_count,

                                    "matched_count":
                                        matched_count
                                },
                                sf
                            )

                        return

                # ========================================================
                # DATASET BİTTİ
                # ========================================================

                print()
                print(
                    "=================================================="
                )

                print(
                    "[OK] Dataset tamamen tarandı."
                )

                print(
                    f"Toplam işlenen: "
                    f"{processed_count}"
                )

                print(
                    f"Toplam eşleşen: "
                    f"{matched_count}"
                )

                print(
                    f"Toplam veri: "
                    f"{total_bytes / (1024**3):.4f} GB"
                )

                if total_bytes < target_bytes:

                    print(
                        f"[!] 10 GB'a ulaşılamadı."
                    )

                    print(
                        "[OK] Bulunan tüm veriler kaydedildi."
                    )

                break

            # ============================================================
            # INTERNET / STREAM HATASI
            # ============================================================

            except (
                requests.exceptions.RequestException,
                ConnectionError,
                TimeoutError,
                OSError
            ) as e:

                print()
                print(
                    "=================================================="
                )

                print(
                    "[!] BAĞLANTI KESİLDİ"
                )

                print(
                    f"Hata: {e}"
                )

                print(
                    "Mevcut durum kaydediliyor..."
                )

                print(
                    "=================================================="
                )

                # --------------------------------------------
                # Dosyaları güvenli şekilde yaz
                # --------------------------------------------

                f.flush()
                csv_f.flush()

                # --------------------------------------------
                # STATE
                # --------------------------------------------

                with open(
                    state_path,
                    'w',
                    encoding='utf-8'
                ) as sf:

                    json.dump(
                        {
                            "processed_count":
                                processed_count,

                            "matched_count":
                                matched_count
                        },
                        sf
                    )

                # --------------------------------------------
                # INTERNET BEKLE
                # --------------------------------------------

                wait_for_internet()

                print(
                    f"[RESUME] "
                    f"{processed_count} kayıt sonrasından "
                    f"devam ediliyor..."
                )

                # --------------------------------------------
                # DATASET YENİDEN BAĞLAN
                # --------------------------------------------

                while True:

                    try:

                        dataset = load_dataset(
                            "togethercomputer/RedPajama-Data-1T",
                            "arxiv",
                            split="train",
                            streaming=True,
                            trust_remote_code=True
                        )

                        dataset = dataset.skip(
                            processed_count
                        )

                        print(
                            "[OK] Stream yeniden bağlandı."
                        )

                        break

                    except Exception as retry_error:

                        print(
                            f"[!] Yeniden bağlantı başarısız: "
                            f"{retry_error}"
                        )

                        wait_for_internet()

            except KeyboardInterrupt:

                print(
                    "\n[!] Ctrl+C algılandı! "
                    "Sistem durduruluyor..."
                )

                with open(
                    state_path,
                    'w',
                    encoding='utf-8'
                ) as sf:

                    json.dump(
                        {
                            "processed_count":
                                processed_count,

                            "matched_count":
                                matched_count
                        },
                        sf
                    )

                break

    finally:

        f.flush()
        csv_f.flush()

        f.close()
        csv_f.close()

        print(
            f"[OK] Kapanış güvenli tamamlandı. "
            f"Toplam işlenen: {processed_count}, "
            f"Toplam eşleşen: {matched_count}"
        )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print(
        "=================================================="
    )

    print(
        "   MASSIVE RADAR CORPUS GENERATOR (STREAMING)"
    )

    print(
        "=================================================="

    )

    stream_and_filter(
        target_gb=10,
        chunk_gb=4.0,
        output_prefix="radar_massive_corpus"
    )