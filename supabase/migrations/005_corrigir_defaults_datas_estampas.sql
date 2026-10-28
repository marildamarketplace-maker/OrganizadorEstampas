-- Garante que inserções feitas pela sincronização REST recebam datas do banco.
-- Algumas instalações antigas já possuíam as colunas, mas sem os defaults
-- definidos pela criação original da tabela.
ALTER TABLE public.estampas
    ALTER COLUMN created_at SET DEFAULT now(),
    ALTER COLUMN updated_at SET DEFAULT now();

NOTIFY pgrst, 'reload schema';
