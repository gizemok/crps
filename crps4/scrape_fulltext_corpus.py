import os
import json
import time
import urllib.request
import urllib.parse
import csv
import re
try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

# ================= KURAL VE YOLLAR =================
CONFIG_FILE = "config.json"
STATE_FILE = "fulltext_state.json"
JSONL_OUTPUT = "fulltext_master.jsonl"
CSV_OUTPUT = "fulltext_sources_index.csv"
DOWNLOAD_DIR = "downloads"

class FullTextScraper:
    def __init__(self):
        if not PYPDF_AVAILABLE:
            raise ImportError("Lutfen once pypdf kurun: pip install pypdf")

        # Config'i oku
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        self.queries = self.config.get("queries", [])
        
        # Hafizayi oku (Kopmalara karsi)
        self.state = {"visited_urls": []}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                self.state = json.load(f)
        self.visited = set(self.state["visited_urls"])
        
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        self.temp_pdf = os.path.join(DOWNLOAD_DIR, "temp_fulltext.pdf")
        
        # CSV Basliklari
        if not os.path.exists(CSV_OUTPUT):
            with open(CSV_OUTPUT, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Source", "Title", "URL", "Snippet", "Matched_Keywords", "Original_PDF_MB"])

    def save_state(self):
        self.state["visited_urls"] = list(self.visited)
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.state, f)

    def extract_pdf_text(self, pdf_url):
        """PDF'i indirir, orijinal boyutunu hesaplar ve icindeki tam metni pypdf ile soker."""
        try:
            req = urllib.request.Request(
                pdf_url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                pdf_data = response.read()

            pdf_bytes = len(pdf_data) # Orijinal dosya boyutu (Bytes)

            with open(self.temp_pdf, 'wb') as f:
                f.write(pdf_data)

            reader = pypdf.PdfReader(self.temp_pdf)
            text_parts = []
            for page in reader.pages:
                t = page.extract_text()
                if t: text_parts.append(t)
            
            full_text = "\n\n".join(text_parts)
            # Bosluklari ve gereksiz satir atlamalarini temizle
            full_text = re.sub(r'\s+', ' ', full_text).strip()
            return full_text, pdf_bytes

        except Exception as e:
            print(f"      [!] PDF Indirme/Okuma Hatasi: {e}")
            return "", 0

    def process_and_save(self, source, title, pdf_url):
        """Makaleyi sizer, radar kalkanindan gecirir ve kaydeder."""
        if pdf_url in self.visited:
            return False
            
        print(f"\n[{source}] Indiriliyor: {title[:50]}...")
        full_text, pdf_bytes = self.extract_pdf_text(pdf_url)
        
        if len(full_text) < 1000:
            print("      [!] PDF cok kisa veya korumali (Paywall). Atlaniyor.")
            self.visited.add(pdf_url)
            self.save_state()
            return False

        text_lower = full_text.lower()
        
        # 1. GLOBAL RADAR KALKANI (Eger metinde radar yoksa direk cöpe at)
        if "radar" not in text_lower:
            print("      [-] Makalede 'radar' kelimesi gecmiyor (Farkli Sektor). Atlaniyor.")
            self.visited.add(pdf_url)
            self.save_state()
            return False
            
        # 2. HEDEF KELIME EŞLEŞTİRMESİ (ESNETİLMİŞ)
        # Ornegin "jpda radar" ariyorsak, yan yana gecmek zorunda degil. 
        # Metnin bir yerinde "jpda" baska bir yerinde "radar" varsa kabul ediyoruz!
        matched_kws = []
        for query in self.queries:
            query_words = query.split()
            if all(w in text_lower for w in query_words):
                matched_kws.append(query)
                
        if not matched_kws:
            print("      [-] Hedef tracking/kinematik kelimeleri bulunamadi. Atlaniyor.")
            self.visited.add(pdf_url)
            self.save_state()
            return False

        # BASARILI! JSONL ve CSV'ye kaydet
        pdf_mb = pdf_bytes / (1024 * 1024)
        print(f"      [SUCCESS] Harika! Eslesti: {matched_kws} | Orijinal PDF: {pdf_mb:.2f} MB")
        
        # JSONL Kayit
        record = {
            "text": f"TITLE: {title}\nURL: {pdf_url}\n\nFULL TEXT:\n{full_text}",
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
            
        # CSV Kayit
        snippet = full_text[:80].replace('\n', ' ') + "..."
        with open(CSV_OUTPUT, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([source, title, pdf_url, snippet, ", ".join(matched_kws), f"{pdf_mb:.2f}"])
            
        self.visited.add(pdf_url)
        self.save_state()
        return True

    # ------------------ KAZIYICILAR (SCRAPERS) ------------------

    def scrape_scholar(self, query):
        """Semantic Scholar acik erisimli (OpenAccess) PDF'leri ceker."""
        encoded = urllib.parse.quote(query)
        url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded}&limit=30&fields=title,url,openAccessPdf"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'RadarBot/1.0'})
            with urllib.request.urlopen(req, timeout=15) as res:
                data = json.loads(res.read().decode('utf-8'))
            
            for paper in data.get('data', []):
                title = paper.get('title')
                oa = paper.get('openAccessPdf')
                if oa and oa.get('url'):
                    # Sadece bedava PDF linki varsa indir
                    self.process_and_save("Scholar", title, oa.get('url'))
                    time.sleep(5) # IP Ban yememek (429 Hatasi) icin beklemeyi artirdik
        except Exception as e:
            print(f"[Scholar] Hata: {query} -> {e}")

    def scrape_hal(self, query):
        """Fransiz HAL Arsivinden PDF ceker."""
        encoded = urllib.parse.quote(query)
        url = f"https://api.archives-ouvertes.fr/search/?q={encoded}&fl=title_s,fileMain_s&rows=30"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'RadarBot/1.0'})
            with urllib.request.urlopen(req, timeout=15) as res:
                data = json.loads(res.read().decode('utf-8'))
                
            for doc in data.get('response', {}).get('docs', []):
                title = doc.get('title_s', [''])[0]
                pdf_url = doc.get('fileMain_s')
                if pdf_url:
                    self.process_and_save("HAL", title, pdf_url)
                    time.sleep(2)
        except Exception as e:
            print(f"[HAL] Hata: {query} -> {e}")

    def scrape_nasa(self, query):
        """NASA NTRS Arsivinden PDF ceker."""
        encoded = urllib.parse.quote(query)
        url = f"https://ntrs.nasa.gov/api/citations/search?q={encoded}&size=30"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'RadarBot/1.0'})
            with urllib.request.urlopen(req, timeout=15) as res:
                data = json.loads(res.read().decode('utf-8'))
                
            for doc in data.get('results', []):
                title = doc.get('title')
                downloads = doc.get('downloads', [])
                for d in downloads:
                    link = d.get('links', {}).get('pdf')
                    if link:
                        pdf_url = f"https://ntrs.nasa.gov{link}"
                        self.process_and_save("NASA", title, pdf_url)
                        time.sleep(2)
                        break
        except Exception as e:
            print(f"[NASA] Hata: {query} -> {e}")

    def run_all(self):
        print("=====================================================")
        print("    TAM METIN KAZIYICI (FULL-TEXT SCRAPER) BASLADI   ")
        print("=====================================================")
        try:
            for query in self.queries:
                print(f"\n>>> ARANIYOR: {query} <<<")
                self.scrape_scholar(query)
                self.scrape_hal(query)
                self.scrape_nasa(query)
        except KeyboardInterrupt:
            print("\n[!] Ctrl+C basildi. Guvenle kapaniyor...")
            self.save_state()
        finally:
            print("\n[OK] Sistem durumu kaydedildi. Islem Tamam!")

if __name__ == "__main__":
    scraper = FullTextScraper()
    scraper.run_all()
