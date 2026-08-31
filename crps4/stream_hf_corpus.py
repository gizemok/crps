import os
import json
import time
import csv
import warnings
warnings.filterwarnings('ignore')

# SSL Hatalarini engelle (Kurum/Ev guvenlik duvarlari icin)
os.environ["HF_HUB_DISABLE_SSL_VERIFICATION"] = "1"
import requests
original_request = requests.Session.request
def patched_request(*args, **kwargs):
    kwargs['verify'] = False
    return original_request(*args, **kwargs)
requests.Session.request = patched_request

from datasets import load_dataset

# =================================================================
# AYARLAR (Bu degeri "wikipedia", "book", "c4" olarak degistirebilirsiniz)
# =================================================================
SUBSET = "wikipedia"  

CONFIG_FILE = "config.json"
DATASET_NAME = "togethercomputer/RedPajama-Data-1T"
JSONL_OUTPUT = f"{SUBSET}_master.jsonl"
STATE_FILE = f"{SUBSET}_stream_state.json"
CSV_OUTPUT = f"{SUBSET}_sources_index.csv"

def get_keywords():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f).get("queries", [])

def run_stream():
    queries = get_keywords()
    print("=========================================================")
    print(f"      HUGGINGFACE DEVASA VERI AKISI BASLIYOR")
    print(f"      HEDEF HAVUZ: {SUBSET.upper()}")
    print("=========================================================")
    
    state = {"processed_count": 0, "saved_count": 0}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
            
    # CSV Basliklari
    if not os.path.exists(CSV_OUTPUT):
        with open(CSV_OUTPUT, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["ID/URL", "Snippet", "Matched_Keywords"])

    print(f"[!] {SUBSET} havuzu internetten baglanarak cekiliyor, lutfen bekleyin...")
    ds = load_dataset(DATASET_NAME, SUBSET, streaming=True, split="train", trust_remote_code=True)
    iterator = iter(ds)
    
    # Eger sistem daha once kapatildiysa, kaldigi yere kadar hizli ileri sar (Fast-Forward)
    if state["processed_count"] > 0:
        print(f"[{SUBSET}] Hafizadan yukleniyor. {state['processed_count']:,} makale hizlica atlanacak (Lutfen bekleyin)...")
        for _ in range(state["processed_count"]):
            try:
                next(iterator)
            except StopIteration:
                break
                
    print("\n>>> AV BASLADI! (Sonsuz Dongudur, kapatmak icin klavyeden Ctrl+C basiniz) <<<")
    
    try:
        while True:
            row = next(iterator)
            state["processed_count"] += 1
            
            # Ekranda 10 binde bir yasadigini belli etsin
            if state["processed_count"] % 10000 == 0:
                print(f"--- Taranan Makale: {state['processed_count']:,} ---")

            text = row.get("text", "")
            if len(text) < 500:
                continue
                
            text_lower = text.lower()
            
            # 1. KURAL: Icerisinde Radar gecmeyen her seyi dogrudan cope at (Performans Kalkanı)
            if "radar" not in text_lower:
                continue
                
            # 2. KURAL: Hedef kelimelerin parcali olarak eslesmesi (Relaxed Match)
            # Ornek: "awacs tracking" kelimelerinden "awacs" ve "tracking" metnin icinde ayri ayri yerlerde gecebilir.
            matched_kws = []
            for query in queries:
                query_words = query.split()
                if all(w in text_lower for w in query_words):
                    matched_kws.append(query)
                    
            if not matched_kws:
                continue
                
            # ============= BASARILI ESLESME (KAYIT) =============
            state["saved_count"] += 1
            meta = row.get("meta", {})
            doc_id = meta.get("url", meta.get("title", f"doc_{state['processed_count']}"))
            
            print(f"      [SUCCESS] {SUBSET.upper()} | Eslesti: {matched_kws}")
            
            # JSONL Kayit
            record = {
                "text": text,
                "meta": {
                    "source": SUBSET,
                    "id": doc_id,
                    "matched_keywords": matched_kws,
                    "hf_meta": meta
                }
            }
            with open(JSONL_OUTPUT, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                
            # CSV Kayit
            snippet = text[:80].replace('\n', ' ') + "..."
            with open(CSV_OUTPUT, 'a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([doc_id, snippet, ", ".join(matched_kws)])
                
            # Her 5 basarili kayitta bir durumu yedekle (Inernet kopmasina karsi)
            if state["saved_count"] % 5 == 0:
                with open(STATE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(state, f)
                    
    except KeyboardInterrupt:
        print("\n[!] Kullanici tarafindan durduruldu (Ctrl+C). Durum kaydediliyor...")
    except StopIteration:
        print("\n[OK] Koca bir havuzun (Okyanusun) sonuna gelindi!")
    except Exception as e:
        print(f"\n[ERROR] Beklenmeyen Hata (Ag kopmasi olabilir): {e}")
    finally:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f)
        print(f"[OK] Hafiza guvenle kaydedildi. Toplam islenen: {state['processed_count']:,}, Yakalanan Radar Dökümanı: {state['saved_count']:,}")

if __name__ == "__main__":
    run_stream()
