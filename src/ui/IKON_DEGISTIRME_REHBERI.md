# 🎨 Suylios Downloader - Uygulama İkonu & Logo Değiştirme Rehberi

Bu rehber, uygulamanın hem **Görev Çubuğu / masaüstü (.exe)** simgesini hem de **Uygulama içi sol üst menü** simgesini nasıl değiştireceğinizi adım adım açıklar.

---

## 1. Masaüstü & Görev Çubuğu Simgesini (.exe İkonunu) Değiştirme

Uygulamayı `build_portable.py` veya `build_setup.py` ile derlediğinizde oluşan `suylios.exe` dosyasının simgesini değiştirmek için:

1. Kullanmak istediğiniz simgeyi **`.ico`** formatına çevirin (Örn: `128x128` veya `256x256` çözünürlükte bir `.ico` dosyası). *Eğer elinizde PNG varsa online ücretsiz "PNG to ICO converter" sitelerini kullanabilirsiniz.*
2. Hazırladığınız `.ico` dosyasının adını tam olarak **`icon.ico`** yapın.
3. Bu dosyayı projenizin içindeki şu konuma yapıştırın:
   ```text
   src/ui/icon.ico
   ```
4. Derleme betiklerinden birini çalıştırın:
   ```bash
   python build_portable.py
   # veya
   python build_setup.py
   ```
   Derleyici (`Nuitka` & `InnoSetup`) arkaplanda `src/ui/icon.ico` dosyasını otomatik olarak algılayıp `.exe` dosyasına ve masaüstü kısayoluna mühürleyecektir!

---

## 2. Uygulama İçi Sol Üst Header Logosunu Değiştirme

Uygulama açıldığında sol üst köşede parlayan siber simgeyi kendi görselinizle değiştirmek isterseniz:

1. Kendi logonuzu PNG veya SVG formatında hazırlayın (Örn: `logo.png`).
2. Dosyayı şu klasöre atın:
   ```text
   src/ui/assets/logo.png
   ```
3. `src/ui/index.html` dosyasını herhangi bir metin editörüyle açın ve **20. satırdaki** şu kısmı:
   ```html
   <svg class="logo-icon" viewBox="0 0 24 24" width="18" height="18">...</svg>
   ```
   Şununla değiştirin:
   ```html
   <img src="assets/logo.png" style="width: 22px; height: 22px; object-fit: contain; border-radius: 4px;">
   ```

Sayfayı yenilediğinizde veya uygulamayı açtığınızda sol üstte doğrudan kendi logonuz parlayacaktır! 🚀
