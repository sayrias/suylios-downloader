# Suylios Downloader

**Yeni Nesil, Gömülü FFmpeg Barındıran ve Hibrit Çoklu Kaynak Destekli Medya İndirme Üssü.**

Suylios Downloader; YouTube akışlarından +18 arşiv platformlarına, büyük dosya barındırma servislerinden (Pixeldrain, Bunkr, Gofile) yüksek kalitede müzik ve video albümlerine kadar her türlü medyayı tek tıkla cihazınıza indirebilmeniz için geliştirilmiş masaüstü canavarıdır.

---

## 🌟 Öne Çıkan Özellikler

- ⚡ **Akıllı Link Yakalama (Ctrl+V):** Uygulama açıkken panonuza kopyaladığınız herhangi bir linki **Ctrl+V** ile yapıştırdığınız an otomatik algılar ve indirmeyi başlatır.
- 🌐 **Sınır Tanımaz Site Desteği:** 
  - **YouTube & +18 Platformlar:** Maksimum çözünürlük (4K/8K) ve kayıpsız ses birleştirme.
  - **Bunkr & balbums.st:** Dinamik albüm arşivi indirme mekanizması.
  - **Pixeldrain:** Parçalı akış motoru ile sıfır bellek şişmesi.
  - **Gofile:** Otomatik oturum senkronizasyonu.
- 🎵 **Anında MP3 / Ses Dönüşümü:** İndirdiğiniz herhangi bir videoyu veya çalma listesini tek tuşla 320 kbps kalitesinde stüdyo MP3 dosyasına çevirir.
- 🎨 **Siber-Estetik Cam Tasarım (Glassmorphism):** Göz yormayan koyu uzay teması, neon odaklanma ışıkları ve 16:9 geniş medya kartları.

---

## 📸 Uygulama Ekran Görüntüleri

*(Ekran fotoğraflarını aşağıdaki rehbere göre çekip proje ana dizinine `screenshots` klasörü açarak içine ekleyebilirsiniz)*

### 1. Ana İndirme Üssü (Ana Sayfa)
`![Ana Sayfa](screenshots/main_page.png)`
> **Nasıl Çekilmeli:** Uygulamanın üst link yapıştırma çubuğu görünürken ve alt kısımda aktif indirme kartları (%100 tamamlanmış veya indirme aşamasında) listelenirken ekranın tam fotoğrafını alın.

### 2. Gelişmiş Ayarlar Menüsü
`![Ayarlar Sekmesi](screenshots/settings_page.png)`
> **Nasıl Çekilmeli:** Sol üstteki dişli (Ayarlar) butonuna tıklayıp yan menüden **Genel Ayarlar** veya **Ağ Ayarları** sekmesi açıkken ekran fotoğrafı alın.

### 3. Geniş Medya Kartı Detayı
`![Medya Kartı](screenshots/media_card.png)`
> **Nasıl Çekilmeli:** İndirmesi bitmiş, sol tarafında video kapak resmi (thumbnail) parlayan ve sağında yeşil **TAMAMLANDI** rozeti olan tek bir indirme kartını yakından çekin.

---

## 🚀 Nasıl Kullanılır? (Hızlı Başlangıç Rehberi)

### Yöntem A: Panodan Jet İndirme
1. Tarayıcınızda beğendiğiniz videonun, albümün veya dosyanın linkini kopyalayın (`Ctrl+C`).
2. **Suylios Downloader** penceresini açın ve klavyeden direkt **`Ctrl+V`** tuşlarına basın.
3. Link otomatik algılanacak ve medya anında bilgisayarınıza inecektir!

### Yöntem B: Format Özelleştirerek İndirme
1. Kopyaladığınız linki üstteki arama kutusuna yapıştırın.
2. Sağ taraftaki **Format** kutusundan `MP3 (Ses)` veya `MP4 (Video)` seçimi yapın.
3. **Kalite** kutusundan çözünürlüğü (örn. `1080p` veya `En İyi`) belirleyin.
4. Mor renkli **İndir** butonuna basın.

### İndirilen Dosyalara Ulaşma
İndirme kartının sağ alt köşesinde bulunan **Klasör İkonuna** tıkladığınızda dosyanın indiği yerel Windows klasörü anında önünüze açılacaktır.

---

## 🔒 Sistem Güvenliği
Tüm indirme işlemleri yerel cihazınızın kumanda panelinde gerçekleşir. Harici hiçbir bulut sunucusuna veri veya log gönderilmez. FFmpeg motoru uygulama içine gömülü (`bin/ffmpeg.exe`) şekilde tam izolasyonla çalışır.
