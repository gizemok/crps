import sqlite3
import json
import time
import random
import os
import io
import re
import csv
from datetime import datetime
import cloudscraper
import PyPDF2
from bs4 import BeautifulSoup

# Config
DB_PATH = "radar_corpus.db"
JSONL_OUTPUT = "corpus/crps-master_1/radar_massive_corpus_part1.jsonl"
CSV_OUTPUT = "corpus/crps-master_1/radar_sources_index.csv"

def init_scraper():
    return cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )

def extract_pdf_text(scraper, pdf_url):
    try:
        response = scraper.get(pdf_url, timeout=30)
        if response.status_code == 200 and b'%PDF' in response.content[:10]:
            pdf_file = io.BytesIO(response.content)
            reader = PyPDF2.PdfReader(pdf_file)
            text = ""
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
            
            # Gürültü (Noise) temizliği
            text = re.sub(r'\s+', ' ', text).strip()
            return text, len(response.content) / (1024 * 1024)
        else:
            print(f"[!] PDF formatı geçersiz veya 403 Forbidden: {pdf_url}")
            return None, 0
    except Exception as e:
        print(f"[!] PDF İndirme Hatası: {e}")
        return None, 0

def resolve_pdf_url(scraper, source_type, base_url):
    if source_type == 'arxiv':
        # http://arxiv.org/abs/2405.01023v1 -> http://arxiv.org/pdf/2405.01023v1.pdf
        return base_url.replace('/abs/', '/pdf/') + ".pdf"
        
    elif source_type == 'hal':
        # https://hal.science/hal-04921869v1 -> https://hal.science/hal-04921869v1/document
        return base_url.rstrip('/') + "/document"
        
    elif source_type == 'nasa':
        # NTRS (NASA) sayfasini kaziyip PDF linkini bulalim
        try:
            resp = scraper.get(base_url, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                for a in soup.find_all('a', href=True):
                    if '.pdf' in a['href'].lower():
                        link = a['href']
                        if not link.startswith('http'):
                            link = 'https://ntrs.nasa.gov' + link
                        return link
        except Exception as e:
            print(f"[!] NASA Link Çözümleme Hatası: {e}")
    else:
        # Geriye kalan kaynaklar (scholar, dtic, web, pdf vb.) icin linki dogrudan deneriz.
        # Sayfada dogrudan PDF varsa indirecek, HTML ise 'PDF formati gecersiz' diyip atlayacak.
        return base_url
    
    return None

def main():
    print("🚀 Veritabanı Yükseltme (DB to FullText) Operasyonu Başlıyor...")
    
    # JSON dosyasindan URL'leri okuyalim (DB tasimak zorunda kalmamak icin)
    if not os.path.exists("recovered_urls.json"):
        print("[X] HATA: recovered_urls.json dosyası bulunamadı!")
        return
        
    with open("recovered_urls.json", "r", encoding="utf-8") as f:
        rows = json.load(f)
        
    print(f"[*] Toplam {len(rows)} adet potansiyel makale bulundu. İndirme başlıyor...\n")
    
    scraper = init_scraper()
    success_count = 0
    
    for row in rows:
        doc_id = row["id"]
        source_type = row["source_type"]
        title = row["title"]
        base_url = row["url_or_path"]
        scraped_at = row["scraped_at"]
        
        print(f"-> İşleniyor ({source_type.upper()}): {title[:60]}...")
        
        pdf_url = resolve_pdf_url(scraper, source_type, base_url)
        if not pdf_url:
            print("   [!] PDF linki çözümlenemedi, atlanıyor.")
            continue
            
        print(f"   [*] PDF İndiriliyor: {pdf_url}")
        
        # Insan taklidi gecikme (Rate Limit engeli icin)
        time.sleep(random.uniform(2.5, 4.5))
        
        text, size_mb = extract_pdf_text(scraper, pdf_url)
        
        if text and len(text) > 1000: # En az 1000 karakter olmali
            # 1. JSONL Dosyasina Ekle
            record = {
                "text": text,
                "meta": {
                    "source": source_type.upper(),
                    "title": title,
                    "url": base_url,
                    "pdf_url": pdf_url,
                    "recovered_from_db": True,
                    "timestamp": datetime.now().isoformat()
                }
            }
            
            with open(JSONL_OUTPUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                
            # 2. CSV Indeksine Ekle
            file_exists = os.path.exists(CSV_OUTPUT)
            with open(CSV_OUTPUT, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Source", "Title", "URL", "Snippet", "Matched_Keywords", "Original_PDF_MB"])
                
                snippet = text[:200].replace('\n', ' ') + "..."
                writer.writerow([source_type.upper(), title, base_url, snippet, "RECOVERED_FROM_DB", round(size_mb, 2)])
                
            print(f"   [+] BAŞARILI! Metin boyutu: {len(text)} karakter. (JSONL ve CSV güncellendi)")
            success_count += 1
        else:
            print("   [-] PDF metni çok kısa veya korumalı.")
            
    print(f"\n✅ OPERASYON TAMAMLANDI! {success_count} makale başarıyla Tam Metne dönüştürülüp mevcut Dashboard'a eklendi.")

if __name__ == "__main__":
    main()
