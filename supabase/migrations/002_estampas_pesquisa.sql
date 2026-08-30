-- Estrutura compartilhada de pesquisa para o indexador e o sistema Next.js.
-- O indexador não envia nem sobrescreve os campos editoriais/IA abaixo.

ALTER TABLE public.estampas ADD COLUMN IF NOT EXISTS titulo text;
ALTER TABLE public.estampas ADD COLUMN IF NOT EXISTS descricao text;
ALTER TABLE public.estampas ADD COLUMN IF NOT EXISTS tema text;

ALTER TABLE public.estampas
    ADD COLUMN IF NOT EXISTS palavras_chave text[] NOT NULL DEFAULT '{}';
ALTER TABLE public.estampas
    ADD COLUMN IF NOT EXISTS cores text[] NOT NULL DEFAULT '{}';
ALTER TABLE public.estampas
    ADD COLUMN IF NOT EXISTS elementos_visuais text[] NOT NULL DEFAULT '{}';
ALTER TABLE public.estampas
    ADD COLUMN IF NOT EXISTS ocasioes text[] NOT NULL DEFAULT '{}';
ALTER TABLE public.estampas
    ADD COLUMN IF NOT EXISTS categorias text[] NOT NULL DEFAULT '{}';

ALTER TABLE public.estampas ADD COLUMN IF NOT EXISTS texto_pesquisa text;
ALTER TABLE public.estampas
    ADD COLUMN IF NOT EXISTS ai_metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.estampas ADD COLUMN IF NOT EXISTS ai_processed_hash text;
ALTER TABLE public.estampas ADD COLUMN IF NOT EXISTS processing_error text;
ALTER TABLE public.estampas ADD COLUMN IF NOT EXISTS processed_at timestamptz;
ALTER TABLE public.estampas ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- Pesos: identificação comercial > título/tema > descrição > texto agregado.
-- O texto agregado pode conter palavras-chave, cores, elementos, ocasiões e
-- categorias preparados pelo Next.js, sem acoplar o banco ao formato da IA.
CREATE OR REPLACE FUNCTION public.estampas_atualizar_search_vector()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('simple', coalesce(NEW.codigo, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(NEW.variante, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(NEW.titulo, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(NEW.tema, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(NEW.descricao, '')), 'C') ||
        setweight(to_tsvector('simple', coalesce(NEW.texto_pesquisa, '')), 'D');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS estampas_atualizar_search_vector_trigger ON public.estampas;
CREATE TRIGGER estampas_atualizar_search_vector_trigger
BEFORE INSERT OR UPDATE OF
    codigo, variante, titulo, descricao, tema, texto_pesquisa
ON public.estampas
FOR EACH ROW EXECUTE FUNCTION public.estampas_atualizar_search_vector();

-- Preenche o vetor dos registros que já existiam antes desta migração.
UPDATE public.estampas
SET search_vector =
    setweight(to_tsvector('simple', coalesce(codigo, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(variante, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(titulo, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(tema, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(descricao, '')), 'C') ||
    setweight(to_tsvector('simple', coalesce(texto_pesquisa, '')), 'D');

CREATE INDEX IF NOT EXISTS estampas_search_vector_gin
    ON public.estampas USING gin (search_vector);

-- Índices úteis para filtros combinados com a busca textual.
CREATE INDEX IF NOT EXISTS estampas_processing_status_idx
    ON public.estampas (processing_status);
CREATE INDEX IF NOT EXISTS estampas_ai_processed_hash_idx
    ON public.estampas (ai_processed_hash);
CREATE INDEX IF NOT EXISTS estampas_palavras_chave_gin
    ON public.estampas USING gin (palavras_chave);
CREATE INDEX IF NOT EXISTS estampas_cores_gin
    ON public.estampas USING gin (cores);
CREATE INDEX IF NOT EXISTS estampas_categorias_gin
    ON public.estampas USING gin (categorias);

COMMENT ON COLUMN public.estampas.search_vector IS
    'Vetor FTS mantido pelo banco; consultar com websearch_to_tsquery(''simple'', consulta).';
COMMENT ON COLUMN public.estampas.ai_processed_hash IS
    'Hash do original efetivamente processado pela IA; pertence ao sistema Next.js.';
COMMENT ON COLUMN public.estampas.ai_metadata IS
    'Metadados livres da IA; pertence ao sistema Next.js e não é alterado pelo indexador.';
