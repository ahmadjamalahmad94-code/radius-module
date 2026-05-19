-- 013_card_batches_advradius_ext
-- RM-H4: توسعة card_batches بحقول AdvRadius (Hybrid: أعمدة DB + metadata JSON)

-- ── سياسة توليد كلمة المرور ──
-- password_charset موجود (digits/alpha/mixed). نضيف نمطًا أوسع.
ALTER TABLE card_batches ADD COLUMN password_generation_type TEXT NOT NULL DEFAULT 'medium';
ALTER TABLE card_batches ADD COLUMN random_generation_enabled INTEGER NOT NULL DEFAULT 1;

-- ── prefix/suffix من جهة ──
-- username_prefix و username_suffix موجودان. نضيف selector واحد للجهة.
ALTER TABLE card_batches ADD COLUMN starts_with_or_ends_with TEXT NOT NULL DEFAULT '';
ALTER TABLE card_batches ADD COLUMN prefix_or_suffix_value TEXT NOT NULL DEFAULT '';

-- ── الوقت/الصلاحية ──
-- expire_at و validity_after_first_login_days موجودان.
-- نضيف time_value/unit للحساب الديناميكي + duration_mode.
ALTER TABLE card_batches ADD COLUMN time_value INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_batches ADD COLUMN time_unit TEXT NOT NULL DEFAULT 'days';
ALTER TABLE card_batches ADD COLUMN device_count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE card_batches ADD COLUMN duration_mode TEXT NOT NULL DEFAULT 'time_unit';

-- ── سلوك إضافي ──
ALTER TABLE card_batches ADD COLUMN auto_renew_after_first_use INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_batches ADD COLUMN transfer_to_student_status_on_connect INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_batches ADD COLUMN close_user_session_on_disconnect INTEGER NOT NULL DEFAULT 0;
ALTER TABLE card_batches ADD COLUMN allow_entry_by_previous_card_palestine INTEGER NOT NULL DEFAULT 0;

-- ── السعر الإجمالي (مرجعي) ──
ALTER TABLE card_batches ADD COLUMN total_price REAL NOT NULL DEFAULT 0;

-- ── metadata JSON للحقول المستقبلية ──
ALTER TABLE card_batches ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}';
