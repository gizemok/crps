import os
import time
import json
import requests
import arxiv
import pandas as pd
from urllib.parse import unquote
try:
    from duckduckgo_search import DDGS
    import pymupdf4llm
except ImportError:
    print("❌ HATA: Gerekli kütüphaneler bulunamadı.")
    print("Lütfen terminalde şu komutu çalıştırın: pip install -r requirements_home_pc.txt")
    exit(1)

# ==========================================
# 1. AYARLAR VE ANAHTAR KELİMELER
# ==========================================
PDF_DIR = "raw_pdfs"
OUTPUT_JSONL = "advanced_radar_uav_corpus.jsonl"
OUTPUT_CSV = "advanced_radar_uav_corpus.csv"
os.makedirs(PDF_DIR, exist_ok=True)

# İndirme limitleri (Ev internetinizi ve disk alanınızı yormamak için)
MAX_ARXIV_RESULTS = 5
MAX_OSINT_RESULTS = 3

# ArXiv Akademik Terimler (Havacılık, Radar ve EW Eksenli Dev Kapsam)
# Hiçbir literatür boşluğu bırakmamak için tüm radar alt disiplinleri eklendi.
ARXIV_QUERIES = [
    # 0. Temel Kavramlar (Radar & Aviation 101 - Olmazsa Olmaz)
    '"Introduction to Radar Systems"',
    '"Radar Fundamentals" OR "Principles of Radar"',
    '"Aviation Radar Basics" OR "Airborne Radar"',
    '"Unmanned Aerial Vehicles" AND "Fundamentals"',
    
    # 1. Anten ve Donanım (Antenna & Hardware)
    '"Active Electronically Scanned Array" OR "AESA"',
    '"Passive Electronically Scanned Array" OR "PESA"',
    '"Transmit/Receive Module" AND "GaN" AND "Radar"',
    '"Monopulse Tracking" AND "Radar"',
    # 2. Sinyal İşleme (Signal Processing)
    '"Space-Time Adaptive Processing" OR "STAP"',
    '"Constant False Alarm Rate" OR "CFAR"',
    '"Pulse Compression" AND "Barker Code"',
    '"Ground Moving Target Indicator" OR "GMTI"',
    '"Frequency Modulated Continuous Wave" OR "FMCW Radar"',
    # 3. Elektronik Harp (Electronic Warfare & ECCM)
    '"Digital Radio Frequency Memory" OR "DRFM Jamming"',
    '"Low Probability of Intercept" OR "LPI Radar"',
    '"Electronic Counter-Countermeasures" OR "ECCM"',
    '"Cross-Eye Jamming" AND "Radar"',
    # 4. Hedef Tespiti ve Kinematik (Targeting)
    '"Non-Cooperative Target Recognition" OR "NCTR"',
    '"Track While Scan" AND "Radar"',
    '"Radar Cross Section" AND "Stealth"',
    # 5. Özel Radarlar ve İHA (UAV & Specialized Radars)
    '"Synthetic Aperture Radar" OR "ISAR"',
    '"Bistatic Radar" OR "Multistatic Radar"',
    '"MIMO Radar" AND "Target Detection"',
    '"Unmanned Combat Aerial Vehicle" OR "UCAV Radar"',
    '"Drone Detection Radar" OR "Counter-UAS"',
]

# OSINT (Dorking) Sorguları - NATO, US DoD, UK MoD, NASA, Havacılık Otoriteleri
# Resmi kurumların yayınladığı doktrin ve konsept raporları (Sıfır Literatür Boşluğu)
OSINT_QUERIES = [
    # Temel Kavramlar (Radar 101 & Doktrinler)
    'filetype:pdf "Radar Fundamentals" OR "Introduction to Radar"',
    'site:nato.int filetype:pdf "Radar Principles" OR "Aviation Basics"',
    
    # NATO Doktrin ve Raporları
    'site:nato.int filetype:pdf "Airborne Early Warning and Control" OR "AWACS"',
    'site:nato.int filetype:pdf "Joint Air Power" OR "Air and Space Power"',
    'site:nato.int filetype:pdf "Electronic Warfare" "Radar"',
    'site:nato.int filetype:pdf "Unmanned Aircraft Systems" "UAS"',
    'site:sto.nato.int filetype:pdf "Radar" OR "AESA"', # NATO Science & Technology Organization
    
    # ABD Savunma Bakanlığı (US DoD) & Kuvvetleri
    'site:defense.gov filetype:pdf "Electronic Attack" OR "Electronic Protection"',
    'site:af.mil filetype:pdf "Airborne Radar" OR "Fighter Radar"',
    'site:af.mil filetype:pdf "BVR" OR "Beyond Visual Range"',
    'site:army.mil filetype:pdf "Air Defense Artillery" "Radar"',
    'site:navy.mil filetype:pdf "Phased Array Radar" OR "SPY-1"',
    
    # Avrupa ve İngiltere Savunma Bakanlığı (UK MoD)
    'site:mod.uk filetype:pdf "Air Command" "Radar"',
    'site:mod.uk filetype:pdf "ISTAR" OR "Intelligence, Surveillance, Target Acquisition"',
    
    # NASA ve Sivil/Askeri Havacılık Otoriteleri
    'site:nasa.gov filetype:pdf "Airborne Synthetic Aperture Radar" OR "AirSAR"',
    'site:nasa.gov filetype:pdf "Aviation Safety" "Weather Radar"',
    'site:nasa.gov filetype:pdf "UAV Integration in National Airspace"',
    
    # Avustralya ve Kanada (Sık Sık Açık Kaynak Rapor Yayınlarlar)
    'site:defence.gov.au filetype:pdf "Airborne Radar" OR "Electronic Warfare"',
    'site:forces.gc.ca filetype:pdf "Air Force" "Radar" OR "RCS"',
    
    # Spesifik Araştırma Enstitüleri (Rand Corp vb.)
    'site:rand.org filetype:pdf "Military Aviation" OR "Airborne Radar"',
    'site:rand.org filetype:pdf "Unmanned Aerial Systems" OR "UCAV"'
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ==========================================
# 2. AKILLI PDF İNDİRME MOTORU
# ==========================================
def download_pdf(url, prefix="OSINT"):
    """Verilen URL'den PDF indirir, bozuk veya sahte dosyaları engeller."""
    try:
        # Dosya adını URL'den güvenli bir şekilde çıkar
        raw_name = unquote(url.split("/")[-1].split("?")[0])
        safe_name = "".join([c for c in raw_name if c.isalnum() or c in ".-_"]).strip()
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
            
        # Çok uzun isimleri kes
        if len(safe_name) > 50:
            safe_name = safe_name[-50:]
            
        filepath = os.path.join(PDF_DIR, f"{prefix}_{safe_name}")
        
        if os.path.exists(filepath):
            print(f"   ⏭️ Zaten mevcut: {safe_name}")
            return filepath
            
        print(f"   📥 İndiriliyor: {safe_name[:40]}...")
        response = requests.get(url, headers=HEADERS, timeout=15, stream=True)
        
        # Dosyanın gerçekten PDF olup olmadığını kontrol et
        content_type = response.headers.get('Content-Type', '')
        if response.status_code == 200 and 'application/pdf' in content_type.lower():
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            time.sleep(1) # Banlanmamak için kısa bekleme
            return filepath
        else:
            print(f"   ⚠️ Geçersiz format veya 404 (İndirilmedi): {url[:40]}...")
            return None
            
    except Exception as e:
        print(f"   ❌ İndirme Hatası: {e}")
        return None

# ==========================================
# 3. VERİ TOPLAMA BOTLARI (ARXIV & OSINT)
# ==========================================
def fetch_arxiv_papers():
    print(f"\n🚀 [MODÜL 1] ArXiv Akademik Veritabanı Taranıyor...")
    for query in ARXIV_QUERIES:
        print(f"\n🔍 ArXiv Aranıyor: {query}")
        try:
            client = arxiv.Client()
            search = arxiv.Search(query=query, max_results=MAX_ARXIV_RESULTS, sort_by=arxiv.SortCriterion.Relevance)
            for result in client.results(search):
                safe_title = "".join([c for c in result.title if c.isalnum() or c==' ']).replace(" ", "_")[:50]
                filepath = os.path.join(PDF_DIR, f"ArXiv_{safe_title}.pdf")
                if not os.path.exists(filepath):
                    print(f"   📥 İndiriliyor: {result.title[:60]}...")
                    result.download_pdf(dirpath=PDF_DIR, filename=f"ArXiv_{safe_title}.pdf")
                    time.sleep(1.5)
                else:
                    print(f"   ⏭️ Zaten mevcut: {result.title[:40]}")
        except Exception as e:
            print(f"   ❌ ArXiv Hatası: {e}")

def fetch_osint_papers():
    print(f"\n🌍 [MODÜL 2] Global OSINT (Açık Kaynak İstihbarat) Botu Devrede...")
    print("NATO, NASA, US DoD (.mil) ve UK MoD sitelerine sızılıyor...")
    
    with DDGS() as ddgs:
        for query in OSINT_QUERIES:
            print(f"\n🕵️ Dork Atılıyor: {query}")
            try:
                # DuckDuckGo üzerinden arama yapıyoruz
                results = list(ddgs.text(query, max_results=MAX_OSINT_RESULTS))
                for res in results:
                    url = res.get('href', '')
                    if url.endswith(".pdf"):
                        download_pdf(url, prefix="OSINT")
                time.sleep(2) # Banlanmamak için bekle
            except Exception as e:
                print(f"   ❌ OSINT Arama Hatası: {e}")

# ==========================================
# 4. YAPAY ZEKA DESTEKLİ PDF OKUMA (TABLO & FORMÜL)
# ==========================================
def parse_pdfs_to_corpus():
    print(f"\n🧠 [MODÜL 3] PyMuPDF4LLM ile Askeri Raporlar ve Formüller Analiz Ediliyor...")
    
    all_pdfs = [os.path.join(PDF_DIR, f) for f in os.listdir(PDF_DIR) if f.endswith(".pdf")]
    
    if not all_pdfs:
        print("❌ Klasörde okunacak PDF bulunamadı!")
        return
        
    corpus_data = []
    
    for pdf_path in all_pdfs:
        print(f"   📄 Çevriliyor: {os.path.basename(pdf_path)[:50]}")
        try:
            # Markdown'a çevir (Tabloları ve yapıyı korur)
            md_text = pymupdf4llm.to_markdown(pdf_path)
            
            # Makaleyi mantıksal chunk'lara (parçalara) bölelim
            chunk_size = 3000
            for i in range(0, len(md_text), chunk_size):
                chunk = md_text[i:i+chunk_size]
                if len(chunk.strip()) > 300: # Sadece dolu parçaları al
                    corpus_data.append({
                        "source": os.path.basename(pdf_path),
                        "content": chunk.strip()
                    })
        except Exception as e:
            print(f"   ❌ Okuma hatası ({os.path.basename(pdf_path)[:30]}): {e}")

    # JSONL ve CSV Kaydı
    print(f"\n💾 Veriler JSONL ve CSV formatında kaydediliyor...")
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for item in corpus_data:
            f.write(json.dumps({"text": item["content"]}, ensure_ascii=False) + "\n")
            
    pd.DataFrame(corpus_data).to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    
    print("\n===================================================================")
    print(f"✅ GÖREV TAMAMLANDI!")
    print(f"📊 Toplam {len(corpus_data)} adet askeri veri bloğu (Chunk) oluşturuldu.")
    print(f"📁 Çıktılar: '{OUTPUT_JSONL}' ve '{OUTPUT_CSV}'")
    print("===================================================================")

if __name__ == "__main__":
    fetch_arxiv_papers()
    fetch_osint_papers()
    parse_pdfs_to_corpus()
