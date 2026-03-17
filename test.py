from pipeline.supabase_client import get_client
c = get_client()
try:
    r = c.table('player_match_stats').select('id').limit(1).execute()
    print('Tabela OK. Linhas:', r.data)
except Exception as e:
    print('ERRO — tabela nao existe:', e)
