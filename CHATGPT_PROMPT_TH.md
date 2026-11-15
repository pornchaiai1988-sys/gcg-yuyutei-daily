# ข้อความสำหรับ ChatGPT (คัดลอกไปใช้ได้เลย)

ทุกวัน ให้เปิดไฟล์ CSV สองไฟล์นี้จากลิงก์โดยตรง (อย่าเดาข้อมูล และอย่าใช้ความจำ):

1. การเปลี่ยนแปลงของวันนี้:
   https://raw.githubusercontent.com/pornchaiai1988-sys/gcg-yuyutei-daily/main/data/changes_latest.csv
2. สถานะการสแกน:
   https://raw.githubusercontent.com/pornchaiai1988-sys/gcg-yuyutei-daily/main/data/scan_report_latest.csv
3. (เฉพาะเมื่อต้องดูราคาปัจจุบันของการ์ดใบใดใบหนึ่ง) ราคาทั้งหมด:
   https://raw.githubusercontent.com/pornchaiai1988-sys/gcg-yuyutei-daily/main/data/latest.csv

แล้วสรุปเป็นภาษาไทยสั้น ๆ:
- หมวดที่สแกนไม่สำเร็จ (status ไม่ใช่ OK) และบอกว่าหมวดนั้นยังเป็นข้อมูลเก่า
- การ์ดที่ราคาเปลี่ยนมากที่สุด 10 ใบ (เรียงตามเปอร์เซ็นต์และจำนวนเยน) พร้อมราคาเก่า → ใหม่ และสต็อก
- การ์ดใหม่ (NEW) และการ์ดที่หายไป (NOT_SEEN_TODAY)
- ถ้าไฟล์เปิดไม่ได้หรือวันที่ในไฟล์ (observed_at_bangkok) ไม่ใช่วันนี้ ให้บอกตรง ๆ ว่าข้อมูลอาจไม่อัปเดต ห้ามเดาตัวเลข

หมายเหตุ: ราคาเป็นเยนจากหน้าขายของ Yuyu-tei เท่านั้น
