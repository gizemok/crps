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
from datasets import load_dataset

def load_keywords(config_path):
    print(f"Loading keywords from {config_path}...")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            if isinstance(config, list):
                return [kw.lower() for kw in config]
            elif isinstance(config, dict):
                return [kw.lower() for kw in config.get('queries', [])]
            return []
    except Exception as e:
        print(f"Error loading config: {e}")
        return []

def stream_and_filter(target_gb=10, chunk_gb=4.0, output_prefix="radar_massive_corpus", state_file="radar_stream_state.json"):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")
    
    keywords = load_keywords(config_path)
    if not keywords:
        print("No keywords found in config.json. Exiting.")
        return

    print(f"Loaded {len(keywords)} radar keywords. Commencing stream...")
    target_bytes = target_gb * 1024 * 1024 * 1024
    chunk_bytes = chunk_gb * 1024 * 1024 * 1024
    
    processed_count = 0
    matched_count = 0

    state_path = os.path.join(script_dir, state_file)
    if os.path.exists(state_path):
        try:
            with open(state_path, 'r') as sf:
                state = json.load(sf)
                processed_count = state.get("processed_count", 0)
                matched_count = state.get("matched_count", 0)
            print(f"Resuming from existing state: Processed {processed_count} documents.")
        except Exception as e:
            print(f"Warning: Could not read state file: {e}")

    total_bytes = 0
    current_chunk = 1
    
    while os.path.exists(os.path.join(script_dir, f"{output_prefix}_part{current_chunk}.jsonl")):
        size = os.path.getsize(os.path.join(script_dir, f"{output_prefix}_part{current_chunk}.jsonl"))
        if size < chunk_bytes:
            break
        total_bytes += size
        current_chunk += 1
        
    current_file_path = os.path.join(script_dir, f"{output_prefix}_part{current_chunk}.jsonl")
    current_chunk_bytes = 0
    if os.path.exists(current_file_path):
        current_chunk_bytes = os.path.getsize(current_file_path)
        total_bytes += current_chunk_bytes
        
    print(f"Current total collected: {total_bytes / (1024**3):.4f} GB")
    print(f"Writing text to: {output_prefix}_part{current_chunk}.jsonl")

    csv_file_path = os.path.join(script_dir, "radar_sources_index.csv")
    csv_exists = os.path.exists(csv_file_path)
    csv_f = open(csv_file_path, 'a', encoding='utf-8', newline='')
    csv_writer = csv.writer(csv_f)
    if not csv_exists:
        csv_writer.writerow(["Matched_Keywords", "ArXiv_URL", "Timestamp", "Snippet"])
        print("Created new tracking CSV: radar_sources_index.csv")

    dataset = load_dataset(
        "togethercomputer/RedPajama-Data-1T", 
        "arxiv", 
        split="train", 
        streaming=True,
        trust_remote_code=True
    )
    
    if processed_count > 0:
        print("Fast-forwarding stream to the last saved position... (This saves internet bandwidth)")
        dataset = dataset.skip(processed_count)

    start_time = time.time()
    f = open(current_file_path, 'a', encoding='utf-8')

    try:
        for item in dataset:
            text = item.get("text", "")
            if not text:
                continue
            
            processed_count += 1
            text_lower = text.lower()
            
            matched_kws = [kw for kw in keywords if kw in text_lower]
            
            if matched_kws:
                meta = item.get("meta", {})
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except:
                        meta = {}
                        
                json_record = json.dumps({"text": text, "meta": meta})
                record_str = json_record + "\n"
                record_bytes = len(record_str.encode('utf-8'))
                
                if current_chunk_bytes + record_bytes > chunk_bytes:
                    f.close()
                    current_chunk += 1
                    current_file_path = os.path.join(script_dir, f"{output_prefix}_part{current_chunk}.jsonl")
                    print(f"Chunk limit ({chunk_gb} GB) reached. Opening new chunk: {current_file_path}")
                    f = open(current_file_path, 'a', encoding='utf-8')
                    current_chunk_bytes = 0
                    
                f.write(record_str)
                
                url = meta.get("url")
                if not url and "arxiv_id" in meta:
                    url = "https://arxiv.org/abs/" + meta["arxiv_id"]
                if not url:
                    url = "Unknown_URL"
                    
                timestamp = meta.get("timestamp", "Unknown_Time")
                snippet = text[:80].replace("\n", " ").strip() + "..."
                
                csv_writer.writerow([", ".join(matched_kws), url, timestamp, snippet])
                
                matched_count += 1
                current_chunk_bytes += record_bytes
                total_bytes += record_bytes
                
            if processed_count % 1000 == 0:
                elapsed = time.time() - start_time
                gb_collected = total_bytes / (1024**3)
                print(f"[{elapsed:.1f}s] Processed: {processed_count} | Matched: {matched_count} | Size: {gb_collected:.4f} GB / {target_gb} GB")
                f.flush()
                csv_f.flush()
                with open(state_path, 'w') as sf:
                    json.dump({"processed_count": processed_count, "matched_count": matched_count}, sf)

            if total_bytes >= target_bytes:
                print(f"\nSUCCESS: Reached target dataset size of {target_gb} GB!")
                with open(state_path, 'w') as sf:
                    json.dump({"processed_count": processed_count, "matched_count": matched_count}, sf)
                break
    except KeyboardInterrupt:
        print("\n[!] Ctrl+C algilandi! Sistemi durduruyor ve dosyalari guvenle kapatiyorum...")
        with open(state_path, 'w') as sf:
            json.dump({"processed_count": processed_count, "matched_count": matched_count}, sf)
    finally:
        f.close()
        csv_f.close()
        print(f"[OK] Kapanis guvenle tamamlandi. Toplam islenen: {processed_count}, Toplam Eslesen: {matched_count}")

if __name__ == "__main__":
    print("==================================================")
    print("   MASSIVE RADAR CORPUS GENERATOR (STREAMING)     ")
    print("==================================================")
    stream_and_filter(target_gb=10, chunk_gb=4.0, output_prefix="radar_massive_corpus")
