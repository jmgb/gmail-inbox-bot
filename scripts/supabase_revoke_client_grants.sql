-- Retira los grants por defecto de Supabase a anon/authenticated en el schema public.
--
-- Ninguna tabla tiene policies RLS, asi que hoy RLS ya deniega todo a esos roles y el
-- backend accede con la secret/service_role key (ignora grants y RLS). Sin efecto
-- funcional: quita la dependencia de una sola barrera. Si una tabla nueva se crease sin
-- RLS, con los defaults de Supabase quedaria legible y escribible con la clave publica.
-- Auditoria RLS de ~/ai_projects, 2026-10-19.

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;

-- Tablas/secuencias futuras creadas por postgres (SQL editor, CLI, Management API).
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
