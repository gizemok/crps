import os
import json
import uuid
from tqdm import tqdm
import pymupdf4llm

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRAPER_DIR = os.path.dirname(BASE_DIR)
PDF_DIR = os.path.join(SCRAPER_DIR, "downloads", "pdfs")
OUTPUT_JSONL = os.path.join(BASE_DIR, "qwen_finetune_corpus.jsonl")
PROCESSED_LOG = os.path.join(BASE_DIR, "processed_files.log")

CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200

def get_processed_files():
    if not os.path.exists(PROCESSED_LOG):
        return set()
    with open(PROCESSED_LOG, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def log_processed_file(filename):
    with open(PROCESSED_LOG, "a", encoding="utf-8") as f:
        f.write(filename + "\n")

def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP, min_chunk_len=200):
    # Anlamsal (Semantic) Parçalama: Paragraflara, tablolara ve başlıklara saygı duyar (\n\n)
    blocks = text.split("\n\n")
    chunks = []
    current_blocks = []
    current_len = 0
    
    for block in blocks:
        block = block.strip()
        if not block:
            continue
            
        block_len = len(block)
        
        # 1. İstisna Durumu: Dev bir tablo veya çok uzun bir blok (chunk_size'dan büyük)
        if block_len > chunk_size:
            # Mevcut birikimi kaydet
            if current_blocks and current_len > min_chunk_len:
                chunks.append("\n\n".join(current_blocks))
            current_blocks = []
            current_len = 0
            
            # Dev bloğu mecburen satır satır böl (Fallback)
            lines = block.split("\n")
            sub_chunk_lines = []
            sub_len = 0
            for line in lines:
                if sub_len + len(line) + 1 <= chunk_size:
                    sub_chunk_lines.append(line)
                    sub_len += len(line) + 1
                else:
                    if sub_len > min_chunk_len:
                        chunks.append("\n".join(sub_chunk_lines))
                    
                    # Satır bazlı overlap (Örtüşme)
                    overlap_len = 0
                    overlap_lines = []
                    for prev_line in reversed(sub_chunk_lines):
                        if overlap_len + len(prev_line) + 1 <= overlap:
                            overlap_lines.insert(0, prev_line)
                            overlap_len += len(prev_line) + 1
                        else:
                            break
                            
                    sub_chunk_lines = overlap_lines + [line]
                    sub_len = overlap_len + len(line) + (1 if overlap_lines else 0)
            
            if sub_len > min_chunk_len:
                chunks.append("\n".join(sub_chunk_lines))
                
            continue

        # 2. Normal Durum: Blok mevcut chunk'a sığıyorsa ekle
        if current_len + block_len + (2 if current_blocks else 0) <= chunk_size:
            current_blocks.append(block)
            current_len += block_len + (2 if current_blocks else 0)
        else:
            # Sığmıyorsa mevcut chunk'ı kaydet
            if current_len > min_chunk_len:
                chunks.append("\n\n".join(current_blocks))
                
            # Gerçek Overlap (Örtüşme) mantığı: Önceki bloklardan overlap sınırına kadar olanları yeni chunk'a taşı
            overlap_len = 0
            overlap_blocks = []
            for prev_block in reversed(current_blocks):
                if overlap_len + len(prev_block) + 2 <= overlap:
                    overlap_blocks.insert(0, prev_block)
                    overlap_len += len(prev_block) + 2
                else:
                    break
                    
            current_blocks = overlap_blocks + [block]
            current_len = overlap_len + block_len + (2 if overlap_blocks else 0)
            
    # Kalan son chunk'ı kaydet
    if current_len > min_chunk_len:
        chunks.append("\n\n".join(current_blocks))
        
    return chunks

def main():
    print("="*60)
    print("  PYMUPDF4LLM TABANLI PDF -> JSONL PARSER (QWEN FINE-TUNE)")
    print("="*60)

    if not os.path.exists(PDF_DIR):
        print(f"Hata: PDF klasörü bulunamadı -> {PDF_DIR}")
        return

    pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
    if not pdf_files:
        print("Hata: İşlenecek PDF dosyası bulunamadı.")
        return

    processed_files = get_processed_files()
    files_to_process = [f for f in pdf_files if f not in processed_files]
    
    print(f"Toplam PDF: {len(pdf_files)}")
    print(f"Daha önce işlenen: {len(processed_files)}")
    print(f"Şimdi işlenecek: {len(files_to_process)}\n")

    if not files_to_process:
        print("Tüm dosyalar zaten işlenmiş!")
        return

    total_chunks_added = 0

    with open(OUTPUT_JSONL, "a", encoding="utf-8") as out_f:
        for filename in tqdm(files_to_process, desc="PDF'ler İşleniyor", unit="pdf"):
            pdf_path = os.path.join(PDF_DIR, filename)
            try:
                # pymupdf4llm ile PDF'i Markdown (Tablo ve Düzen korunmuş şekilde) dönüştür
                md_text = pymupdf4llm.to_markdown(pdf_path)
                
                # Metni parçalara (chunk) böl
                chunks = chunk_text(md_text, CHUNK_SIZE, CHUNK_OVERLAP)
                
                # CPT (Continuous Pre-Training) formatında JSONL'e yaz
                for chunk in chunks:
                    record = {
                        "id": str(uuid.uuid4()),
                        "source": filename,
                        "text": chunk
                    }
                    out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    total_chunks_added += 1
                
                # İşlenen dosyayı kaydet (Resume özelliği için)
                log_processed_file(filename)
                
            except Exception as e:
                # Hatalı dosyaları atla ama logla
                tqdm.write(f"Hata: {filename} işlenirken bir sorun oluştu -> {e}")

    print(f"\nİşlem Tamamlandı! Toplam {total_chunks_added} adet eğitim parçası (chunk) oluşturuldu.")
    print(f"Çıktı Dosyası: {OUTPUT_JSONL}")

if __name__ == "__main__":
    main()
