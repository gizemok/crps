import os
import json
import time
import random
import csv
import re
import urllib.parse
import traceback

try:
    import cloudscraper
except ImportError:
    raise ImportError("Lütfen önce cloudscraper kurun: pip install cloudscraper")

try:
    from bs4 import BeautifulSoup
except ImportError:
    raise ImportError("Lütfen önce beautifulsoup4 kurun: pip install beautifulsoup4")

try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False
    print("[!] pypdf bulunamadı. Sadece OCR kullanılacak.")

# OCR / Docling Fallbacks
try:
    from pdf2image import convert_from_path
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("[!] pdf2image veya pytesseract bulunamadı. OCR devre dışı.")

try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    print("[!] docling bulunamadı. Gelişmiş belge dönüştürme devre dışı.")

# ================= KURAL VE YOLLAR =================
CONFIG_FILE = "military_config.json"
STATE_FILE = "military_state.json"
JSONL_OUTPUT = "military_radar_corpus.jsonl"
CSV_OUTPUT = "military_sources_index.csv"
DOWNLOAD_DIR = "military_downloads"

class MilitaryDoctrineScraper:
    def __init__(self):
        # Config'i oku
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        self.search_queries = self.config.get("search_queries", ["radar"])
        self.tagging_keywords = self.config.get("tagging_keywords", [])
        
        # Hafizayi oku (Mükerrer kayitlari engeller)
        self.state = {"visited_urls": []}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                self.state = json.load(f)
        self.visited = set(self.state["visited_urls"])
        
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        self.temp_pdf = os.path.join(DOWNLOAD_DIR, "temp_military.pdf")
        
        # CSV Basliklari
        if not os.path.exists(CSV_OUTPUT):
            with open(CSV_OUTPUT, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Source", "Title", "URL", "Snippet", "Matched_Keywords", "Original_PDF_MB"])

        # STEALTH DOWNLOADER ENGINE
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )
        self.base_headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        }
        
        if DOCLING_AVAILABLE:
            self.doc_converter = DocumentConverter()

    def save_state(self):
        self.state["visited_urls"] = list(self.visited)
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.state, f)

    def extract_pdf_text(self, pdf_url):
        """PDF'i indirir, boyutunu hesaplar ve metne cevirip PDF'i siler."""
        try:
            res = self.scraper.get(pdf_url, headers=self.base_headers, timeout=30)
            
            if res.status_code != 200:
                print(f"      [!] PDF Indirme Hatasi. Kod: {res.status_code}")
                return "", 0

            pdf_data = res.content
            pdf_bytes = len(pdf_data)

            with open(self.temp_pdf, 'wb') as f:
                f.write(pdf_data)

            full_text = ""
            
            # AŞAMA 1: PyPDF ile hızlı tarama
            if PYPDF_AVAILABLE:
                try:
                    reader = pypdf.PdfReader(self.temp_pdf)
                    text_parts = []
                    for page in reader.pages:
                        t = page.extract_text()
                        if t: text_parts.append(t)
                    full_text = "\n\n".join(text_parts)
                except Exception as e:
                    print(f"      [!] PyPDF okuma hatasi: {e}")

            # AŞAMA 2: Eğer metin çok azsa veya okunmamışsa OCR/Docling kullan
            if len(full_text.strip()) < 500:
                print("      [*] PyPDF yetersiz kaldi veya gorsel PDF. Gelismis cikarima geciliyor...")
                if DOCLING_AVAILABLE:
                    try:
                        doc = self.doc_converter.convert(self.temp_pdf)
                        full_text = doc.document.export_to_markdown()
                    except Exception as e:
                        print(f"      [!] Docling hatasi: {e}")
                
                # Docling yoksa veya basarisiz olduysa OCR dene
                if len(full_text.strip()) < 500 and OCR_AVAILABLE:
                    try:
                        images = convert_from_path(self.temp_pdf)
                        ocr_text_parts = []
                        for i, img in enumerate(images):
                            if i > 20: # Ilk 20 sayfayi OCR yap (Performans icin)
                                break
                            text = pytesseract.image_to_string(img)
                            ocr_text_parts.append(text)
                        full_text = "\n\n".join(ocr_text_parts)
                    except Exception as e:
                        print(f"      [!] OCR hatasi: {e}")

            full_text = re.sub(r'\s+', ' ', full_text).strip()
            
            # Dosyayi isledikten sonra disk alanindan tasarruf icin sil
            if os.path.exists(self.temp_pdf):
                os.remove(self.temp_pdf)
                
            return full_text, pdf_bytes

        except Exception as e:
            print(f"      [!] PDF Isleme Hatasi: {e}")
            if os.path.exists(self.temp_pdf):
                os.remove(self.temp_pdf)
            return "", 0

    def process_and_save(self, source, title, pdf_url):
        """Belgeyi isler, hedef kelimeleri arar ve JSONL/CSV'ye kaydeder."""
        if pdf_url in self.visited:
            return False
            
        print(f"\n[{source}] Inceleniyor: {title[:60]}...")
        full_text, pdf_bytes = self.extract_pdf_text(pdf_url)
        
        if len(full_text) < 500:
            print("      [-] Belge cok kisa, yalnizca ozet veya korumali. Atlaniyor.")
            self.visited.add(pdf_url)
            self.save_state()
            return False

        text_lower = full_text.lower()
        
        # 1. KATI RADAR KALKANI
        if "radar" not in text_lower:
            print("      [-] Makalede 'radar' kavrami gecmiyor. Atlaniyor.")
            self.visited.add(pdf_url)
            self.save_state()
            return False
            
        # 2. HEDEF KELIME EŞLEŞTİRMESİ (Ayrı kelimeler olarak arar, cümle olarak değil)
        matched_kws = []
        for kw in self.tagging_keywords:
            kw_words = kw.split()
            # Eğer anahtar kelimenin (örn: "joint probabilistic data association") tüm kelimeleri
            # belgenin herhangi bir yerinde geçiyorsa, bu bir eşleşmedir. Yan yana olmak zorunda değil!
            if all(re.search(rf'\b{re.escape(w)}\b', text_lower) for w in kw_words):
                matched_kws.append(kw)
                
        if not matched_kws:
            print("      [-] Hedef kilit kelimeler bulunamadi (Belge radar içeriyor ama spesifik bir konumuzla eşleşmedi). Atlaniyor.")
            self.visited.add(pdf_url)
            self.save_state()
            return False

        # BASARILI!
        pdf_mb = pdf_bytes / (1024 * 1024)
        print(f"      [SUCCESS] Harika Eşleşme! {matched_kws} | Dosya: {pdf_mb:.2f} MB")
        
        record = {
            "text": f"TITLE: {title}\nURL: {pdf_url}\nSOURCE: {source}\n\nFULL TEXT:\n{full_text}",
            "meta": {
                "title": title,
                "url": pdf_url,
                "source": source,
                "matched_keywords": matched_kws,
                "original_pdf_bytes": pdf_bytes
            }
        }
        
        with open(JSONL_OUTPUT, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            
        snippet = full_text[:100].replace('\n', ' ') + "..."
        with open(CSV_OUTPUT, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([source, title, pdf_url, snippet, ", ".join(matched_kws), f"{pdf_mb:.2f}"])
            
        self.visited.add(pdf_url)
        self.save_state()
        return True

    def random_sleep(self, min_s=3, max_s=7):
        time.sleep(random.uniform(min_s, max_s))

    def search_duckduckgo_pdfs(self, query, source_name, site_filter):
        """DuckDuckGo uzerinden site tabanli PDF aramasi (Orn: site:nato.int radar pdf)"""
        print(f"[{source_name}] DuckDuckGo aramasi: {query}")
        search_term = f"site:{site_filter} {query} filetype:pdf"
        encoded_query = urllib.parse.quote(search_term)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        
        try:
            res = self.scraper.get(url, headers=self.base_headers, timeout=15)
            if res.status_code != 200:
                print(f"[{source_name}] DDG Arama Hatasi: {res.status_code}")
                return

            soup = BeautifulSoup(res.text, 'html.parser')
            results = soup.find_all('a', class_='result__url')
            titles = soup.find_all('h2', class_='result__title')
            
            for idx, result in enumerate(results):
                pdf_url = result.get('href')
                if not pdf_url: continue
                
                # DDG bazen yonlendirme linkleri verir, onlari temizle
                if "uddg=" in pdf_url:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(pdf_url).query)
                    if 'uddg' in parsed:
                        pdf_url = parsed['uddg'][0]
                
                if not pdf_url.lower().endswith(".pdf"):
                    continue
                    
                title_text = "Unknown Title"
                if idx < len(titles):
                    title_text = titles[idx].text.strip()

                self.process_and_save(source_name, title_text, pdf_url)
                self.random_sleep(4, 8)
                
        except Exception as e:
            print(f"[{source_name}] Arama motoru hatasi: {e}")

    def run_all(self):
        print("=========================================================")
        print("    MILITARY & DOCTRINE RADAR SCRAPER")
        print("    Kaynaklar: NATO, US Army, DTIC, RAND")
        print("    Motor: Docling/OCR Destekli PDF Çıkarıcı")
        print("=========================================================")
        
        # Sadece genis ve kapsayici kelimelerle arama yapalim (Arama motorunu yormamak ve sifir sonuc almamak icin)
        search_terms = self.search_queries

        try:
            for term in search_terms:
                print(f"\n==========================================")
                print(f" ARASTIRILAN KAVRAM: {term}")
                print(f"==========================================")
                
                # Askeri ve Stratejik Kaynaklar
                self.search_duckduckgo_pdfs(term, "NATO", "nato.int")
                self.search_duckduckgo_pdfs(term, "US Army", "army.mil")
                self.search_duckduckgo_pdfs(term, "DTIC", "dtic.mil")
                self.search_duckduckgo_pdfs(term, "RAND Corp", "rand.org")
                
                self.random_sleep(10, 15)
                
        except KeyboardInterrupt:
            print("\n[!] Kullanici tarafindan durduruldu. Veriler guvenle kaydedildi.")
            self.save_state()
        finally:
            print("\n[OK] Sistem durumu kaydedildi. Askeri veri madenciligi tamamlandi!")

if __name__ == "__main__":
    scraper = MilitaryDoctrineScraper()
    scraper.run_all()
