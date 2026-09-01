import os
import json
import time
import random
import csv
import re
import urllib.parse
import xml.etree.ElementTree as ET
try:
    import cloudscraper
except ImportError:
    raise ImportError("Lutfen once cloudscraper kurun: pip install cloudscraper")

try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

# ================= KURAL VE YOLLAR =================
CONFIG_FILE = "config.json"
STATE_FILE = "academic_state.json"
JSONL_OUTPUT = "academic_master.jsonl"
CSV_OUTPUT = "academic_sources_index.csv"
DOWNLOAD_DIR = "academic_downloads"

class AcademicMasterScraper:
    def __init__(self):
        if not PYPDF_AVAILABLE:
            raise ImportError("Lutfen once pypdf kurun: pip install pypdf")

        # Config'i oku
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        self.queries = self.config.get("queries", [])
        
        # Hafizayi oku (Mükerrer kayitlari engeller)
        self.state = {"visited_urls": []}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                self.state = json.load(f)
        self.visited = set(self.state["visited_urls"])
        
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        self.temp_pdf = os.path.join(DOWNLOAD_DIR, "temp_academic.pdf")
        
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
            'Referer': 'https://scholar.google.com/'
        }

    def save_state(self):
        self.state["visited_urls"] = list(self.visited)
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.state, f)

    def extract_pdf_text(self, pdf_url):
        """PDF'i indirir, orijinal boyutunu hesaplar ve pypdf ile soker."""
        try:
            res = self.scraper.get(pdf_url, headers=self.base_headers, timeout=30)
            
            if res.status_code != 200:
                print(f"      [!] PDF Indirme Hatasi. Kod: {res.status_code}")
                if res.status_code == 403:
                    print("      [!] GUVENLIK DUVARI (Firewall) ENGELI! Ikinci bir deneme yapilabilir.")
                return "", 0

            pdf_data = res.content
            pdf_bytes = len(pdf_data)

            with open(self.temp_pdf, 'wb') as f:
                f.write(pdf_data)

            reader = pypdf.PdfReader(self.temp_pdf)
            text_parts = []
            for page in reader.pages:
                t = page.extract_text()
                if t: text_parts.append(t)
            
            full_text = "\n\n".join(text_parts)
            full_text = re.sub(r'\s+', ' ', full_text).strip()
            return full_text, pdf_bytes

        except Exception as e:
            print(f"      [!] PDF Indirme/Okuma Hatasi: {e}")
            return "", 0

    def process_and_save(self, source, title, pdf_url):
        """Makaleyi sizer, SIKI RADAR kalkanindan gecirir ve kaydeder."""
        if pdf_url in self.visited:
            return False
            
        print(f"\n[{source}] Indiriliyor: {title[:60]}...")
        full_text, pdf_bytes = self.extract_pdf_text(pdf_url)
        
        if len(full_text) < 1500:
            print("      [-] PDF cok kisa, yalnizca ozet (Abstract) veya korumali. Atlaniyor.")
            self.visited.add(pdf_url)
            self.save_state()
            return False

        text_lower = full_text.lower()
        
        # 1. KATI RADAR KALKANI (Makalenin bilimsel odagi radar degilse cöpe at)
        if "radar" not in text_lower:
            print("      [-] Makalede 'radar' kavrami gecmiyor. Atlaniyor.")
            self.visited.add(pdf_url)
            self.save_state()
            return False
            
        # 2. HEDEF KELIME EŞLEŞTİRMESİ (Regex Kelime Siniri ile)
        matched_kws = []
        for query in self.queries:
            query_words = query.split()
            # Alt-metin eslesmesini önlemek icin Regex \b kullanimi
            if all(re.search(rf'\b{re.escape(w)}\b', text_lower) for w in query_words):
                matched_kws.append(query)
                
        if not matched_kws:
            print("      [-] Hedef kilit kelimeler bulunamadi. Atlaniyor.")
            self.visited.add(pdf_url)
            self.save_state()
            return False

        # BASARILI!
        pdf_mb = pdf_bytes / (1024 * 1024)
        print(f"      [SUCCESS] Harika Eşleşme! {matched_kws} | PDF: {pdf_mb:.2f} MB")
        
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

    # ================== KAZIYICI MODULLER (SCRAPER MODULES) ==================

    def random_sleep(self):
        """Bot korumalarina takilmamak icin rastgele bekleme (Anti-Rate Limit)"""
        time.sleep(random.uniform(2.5, 5.5))

    def scrape_arxiv(self, query):
        """ArXiv API - Kuantum/Fizik/CS makaleleri"""
        encoded = urllib.parse.quote(f"all:{query}")
        url = f"http://export.arxiv.org/api/query?search_query={encoded}&start=0&max_results=10"
        try:
            res = self.scraper.get(url, headers=self.base_headers, timeout=15)
            if res.status_code != 200:
                print(f"[ArXiv] API Hatasi: {res.status_code}")
                return
                
            root = ET.fromstring(res.text)
            for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
                title = entry.find("{http://www.w3.org/2005/Atom}title").text.replace("\n", "")
                pdf_url = ""
                for link in entry.findall("{http://www.w3.org/2005/Atom}link"):
                    if link.attrib.get("title") == "pdf":
                        pdf_url = link.attrib.get("href")
                        break
                
                if pdf_url:
                    if not pdf_url.endswith(".pdf"):
                        pdf_url += ".pdf"
                    self.process_and_save("ArXiv", title, pdf_url)
                    self.random_sleep()
        except Exception as e:
            print(f"[ArXiv] Hata: {query} -> {e}")

    def scrape_scholar(self, query):
        """Semantic Scholar - Dunyadaki Universite Makale ve Tezleri"""
        encoded = urllib.parse.quote(query)
        url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded}&limit=10&fields=title,url,openAccessPdf"
        try:
            res = self.scraper.get(url, headers=self.base_headers, timeout=15)
            if res.status_code == 429:
                print(f"[Scholar] RATE LIMIT (Ban) Hatasi! Bir sure bekleniyor...")
                time.sleep(10)
                return
            elif res.status_code != 200:
                print(f"[Scholar] API Hatasi: {res.status_code}")
                return
                
            data = res.json()
            for paper in data.get('data', []):
                title = paper.get('title')
                oa = paper.get('openAccessPdf')
                if oa and oa.get('url'):
                    self.process_and_save("Scholar", title, oa.get('url'))
                    self.random_sleep()
        except Exception as e:
            print(f"[Scholar] Hata: {query} -> {e}")

    def scrape_hal(self, query):
        """HAL (Fransa) - Avrupa Acik Havacilik ve Muhendislik Arsivi"""
        encoded = urllib.parse.quote(query)
        url = f"https://api.archives-ouvertes.fr/search/?q={encoded}&fl=title_s,fileMain_s&rows=10"
        try:
            res = self.scraper.get(url, headers=self.base_headers, timeout=15)
            if res.status_code != 200:
                print(f"[HAL] API Hatasi: {res.status_code}")
                return
                
            data = res.json()
            for doc in data.get('response', {}).get('docs', []):
                title = doc.get('title_s', [''])[0]
                pdf_url = doc.get('fileMain_s')
                if pdf_url:
                    self.process_and_save("HAL", title, pdf_url)
                    self.random_sleep()
        except Exception as e:
            print(f"[HAL] Hata: {query} -> {e}")

    def scrape_nasa(self, query):
        """NASA NTRS - Amerikan Uzay ve Havacilik Resmi Arsivi"""
        encoded = urllib.parse.quote(query)
        url = f"https://ntrs.nasa.gov/api/citations/search?q={encoded}&size=10"
        try:
            res = self.scraper.get(url, headers=self.base_headers, timeout=15)
            if res.status_code != 200:
                print(f"[NASA] API Hatasi: {res.status_code}")
                return
                
            data = res.json()
            for doc in data.get('results', []):
                title = doc.get('title')
                downloads = doc.get('downloads', [])
                for d in downloads:
                    link = d.get('links', {}).get('pdf')
                    if link:
                        pdf_url = f"https://ntrs.nasa.gov{link}"
                        self.process_and_save("NASA", title, pdf_url)
                        self.random_sleep()
                        break
        except Exception as e:
            print(f"[NASA] Hata: {query} -> {e}")

    def run_all(self):
        print("=========================================================")
        print("    ULTIMATE ACADEMIC MASTER SCRAPER (STEALTH MODE)")
        print("    Kaynaklar: ArXiv, NASA, HAL, Semantic Scholar")
        print("    Motor: Cloudscraper Anti-Bot Bypass")
        print("=========================================================")
        try:
            for query in self.queries:
                print(f"\n==========================================")
                print(f" ARASTIRILAN KAVRAM: {query}")
                print(f"==========================================")
                
                # Her kaynaktan bu kelimeyi arastir
                self.scrape_arxiv(query)
                self.scrape_scholar(query)
                self.scrape_hal(query)
                self.scrape_nasa(query)
                
        except KeyboardInterrupt:
            print("\n[!] Kullanici tarafindan durduruldu. Veriler guvenle kaydedildi.")
            self.save_state()
        finally:
            print("\n[OK] Sistem durumu kaydedildi. Akademik veri madenciligi tamamlandi!")

if __name__ == "__main__":
    scraper = AcademicMasterScraper()
    scraper.run_all()
