# kml2dwg — KML/KMZ → DWG/DXF + PDF (As Plan Drawing FTTH)

## Instalasi (sekali)
    pip install ezdxf matplotlib pyproj
Untuk DWG langsung: pasang **ODA File Converter** (gratis, opendesign.com). Tanpa itu, hasil .dxf dibuka di AutoCAD lalu SAVEAS .dwg.

## Pemakaian
    python kml2dwg.py file.kml -o hasil
    python kml2dwg.py file.kmz -o hasil --odp-type 8 --odc-per-sheet 9
    python kml2dwg.py file.kml --no-osm          # tanpa rumah/nama jalan (offline)
    python kml2dwg.py file.kml --osm-file osm.json

Butuh internet saat jalan: rumah & nama jalan diambil dari OpenStreetMap (Overpass), lalu di-cache.
Output: .dxf, .dwg (jika ODA ada), .pdf (A3: ALL, FEEDER, lalu detail per grup ODC).
Opsi lain: --project, --name, --date, --margin, --dwg-version, --pdf-only.

## Catatan
- Klasifikasi simbol dari nama folder/placemark KML (ODC, ODP, /T, /TE, /TL, CLOSURE, FEEDER).
- Tipe ODP default 8HP (ubah --odp-type). Tiang T9 terdeteksi bila nama memuat "9".
- Garis drop ke rumah tidak ada di KML, jadi tidak digambar.
- Kelengkapan rumah/jalan mengikuti data OSM di area tersebut.
