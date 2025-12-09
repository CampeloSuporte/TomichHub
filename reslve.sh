#!/bin/bash

# 🔧 SOLUÇÃO PASSO A PASSO - SEU PROBLEMA ESPECÍFICO

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  🔧 RESOLVENDO: CELERY BEAT TRAVADO EM 'beat: Starting...'   ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Definir cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_PATH="/opt/crm"

echo -e "${BLUE}Diretório do projeto:${NC} $PROJECT_PATH"
echo ""

# ========================================
# PASSO 1: PARAR PROCESSOS TRAVADOS
# ========================================
echo -e "${YELLOW}[PASSO 1]${NC} Parando processos Celery travados..."
echo ""

# Listar processos
if ps aux | grep -v grep | grep -E "celery|beat" > /dev/null; then
    echo "  Encontrados:"
    ps aux | grep -v grep | grep -E "celery|beat"
    echo ""
    echo -n "  Matando processos... "
    pkill -9 -f "celery beat" 2>/dev/null
    pkill -9 -f "celery worker" 2>/dev/null
    sleep 2
    echo -e "${GREEN}✅${NC}"
else
    echo -e "  ${GREEN}✅${NC} Nenhum processo Celery em execução"
fi

echo ""

# ========================================
# PASSO 2: REMOVER ARQUIVO CORROMPIDO
# ========================================
echo -e "${YELLOW}[PASSO 2]${NC} Removendo arquivo corrompido 'celerybeat-schedule'..."
echo ""

cd "$PROJECT_PATH"

if [ -f "celerybeat-schedule" ] || [ -f ".celerybeat-schedule" ]; then
    echo "  Arquivos encontrados:"
    ls -lh celerybeat-schedule* 2>/dev/null || echo "    (nenhum encontrado)"
    
    echo -n "  Removendo... "
    rm -f celerybeat-schedule*
    echo -e "${GREEN}✅${NC}"
else
    echo -e "  ${GREEN}✅${NC} Nenhum arquivo encontrado"
fi

echo ""

# ========================================
# PASSO 3: VERIFICAR SETTINGS.PY
# ========================================
echo -e "${YELLOW}[PASSO 3]${NC} Verificando crm/settings.py..."
echo ""

if grep -q "CELERY_BEAT_SCHEDULER" crm/settings.py 2>/dev/null; then
    echo -e "  ${RED}❌${NC} CELERY_BEAT_SCHEDULER encontrado em settings.py"
    echo "     Este é o problema! Deve ser removido."
    echo ""
    echo "  Linhas encontradas:"
    grep -n "CELERY_BEAT_SCHEDULER" crm/settings.py
    echo ""
    echo "  📝 FAÇA ISTO:"
    echo "     1. Abra: nano crm/settings.py"
    echo "     2. Procure por: CELERY_BEAT_SCHEDULER"
    echo "     3. REMOVA a linha completamente"
    echo "     4. Salve (Ctrl+O, Enter, Ctrl+X)"
    echo ""
    echo "  ⚠️  NÃO CONTINUE ATÉ REMOVER ESTA LINHA!"
    echo ""
    read -p "  Pressione ENTER quando terminar a edição... "
else
    echo -e "  ${GREEN}✅${NC} CELERY_BEAT_SCHEDULER não encontrado (OK)"
fi

echo ""

# ========================================
# PASSO 4: VERIFICAR CELERY.PY
# ========================================
echo -e "${YELLOW}[PASSO 4]${NC} Verificando crm/celery.py..."
echo ""

if grep -q "beat_schedule" crm/celery.py 2>/dev/null; then
    echo -e "  ${GREEN}✅${NC} beat_schedule encontrado em celery.py"
else
    echo -e "  ${YELLOW}⚠️${NC} beat_schedule NÃO encontrado em celery.py"
    echo "     Isso pode ser o problema!"
    echo ""
    echo "  📝 Atualizando celery.py..."
    
    # Copiar arquivo corrigido
    if [ -f "celery_corrigido_v2.py" ]; then
        cp celery_corrigido_v2.py crm/celery.py
        echo -e "     ${GREEN}✅${NC} celery.py atualizado"
    else
        echo -e "     ${RED}❌${NC} celery_corrigido_v2.py não encontrado!"
        echo "        Baixe o arquivo e tente novamente"
        exit 1
    fi
fi

echo ""

# ========================================
# PASSO 5: TESTAR DJANGO
# ========================================
echo -e "${YELLOW}[PASSO 5]${NC} Testando carregamento do Django..."
echo ""

echo "  Executando: python manage.py check"
if python manage.py check > /dev/null 2>&1; then
    echo -e "  ${GREEN}✅${NC} Django OK"
else
    echo -e "  ${YELLOW}⚠️${NC} Django com aviso (pode ser normal)"
fi

echo ""

# ========================================
# PASSO 6: LISTAR TASKS
# ========================================
echo -e "${YELLOW}[PASSO 6]${NC} Verificando tasks Celery..."
echo ""

echo "  Executando: python manage.py shell"
python manage.py shell << 'PYTHON_EOF'
from crm.celery import app

if hasattr(app.conf, 'beat_schedule'):
    print(f"  ✅ beat_schedule com {len(app.conf.beat_schedule)} tasks:")
    for name in app.conf.beat_schedule:
        print(f"     - {name}")
else:
    print("  ❌ beat_schedule não encontrado!")
PYTHON_EOF

echo ""

# ========================================
# PASSO 7: INICIAR CELERY BEAT
# ========================================
echo -e "${YELLOW}[PASSO 7]${NC} Iniciando Celery Beat..."
echo ""

echo "  Comando: celery -A crm beat -l info"
echo ""
echo "  ⏳ Aguardando inicialização (pode levar 10-15 segundos)..."
echo "  ❌ Se travar em 'beat: Starting...', pressione Ctrl+C"
echo ""
echo "  ✅ Se vir as 3 tasks listadas, está funcionando!"
echo ""
echo -e "${YELLOW}═══════════════════════════════════════════════════${NC}"
echo ""

# Iniciar em foreground para o usuário ver
cd "$PROJECT_PATH"
celery -A crm beat -l info

# Se chegou aqui (Ctrl+C foi pressionado)
echo ""
echo ""
echo -e "${YELLOW}═══════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Celery Beat interrompido${NC}"
echo ""
echo -e "${YELLOW}PRÓXIMOS PASSOS:${NC}"
echo ""
echo "  1. Se iniciou OK (viu as 3 tasks):"
echo "     ✅ PROBLEMA RESOLVIDO!"
echo "     Deixe rodando em background ou tmux:"
echo "        tmux new-session -d -s celery 'celery -A crm beat -l info'"
echo ""
echo "  2. Se ainda travou em 'beat: Starting...':"
echo "     ❌ Tente com scheduler alternativo:"
echo "        celery -A crm beat -l info --scheduler=celery.beat.EpochNowScheduler"
echo ""
echo "  3. Se tudo falhar:"
echo "     Veja: FIX_CELERY_BEAT_FREEZE.md para troubleshooting"
echo ""
