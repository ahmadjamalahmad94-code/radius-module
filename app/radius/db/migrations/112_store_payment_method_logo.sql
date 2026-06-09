-- المتجر المتقدّم: شعار/أيقونة اختياري لقناة الاستلام (يظهر على بطاقة
-- القناة القابلة للاختيار في صفحة الشحن لدى الزبون). مسار صورة تحت
-- static/uploads/store/logo (نفس آلية QR والوصل). اختياري — فارغ
-- يعني عرض أيقونة افتراضية حسب نوع القناة.

ALTER TABLE store_payment_methods
  ADD COLUMN logo_image_path TEXT NOT NULL DEFAULT '';
