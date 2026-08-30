-- Inclui o diretório relativo na identidade para preservar arquivos homônimos.
-- Atualiza registros existentes sem apagá-los.
UPDATE public.estampas
SET arquivo_id = lower(
    concat_ws(
        '/',
        nullif(trim(both '/' from replace(coalesce(original_relative_path, ''), '\\', '/')), ''),
        regexp_replace(coalesce(original_filename, ''), '\.[^.]+$', '')
    )
);

NOTIFY pgrst, 'reload schema';
