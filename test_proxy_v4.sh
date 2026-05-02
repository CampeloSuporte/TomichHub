#!/bin/bash
# Teste V4: valida se sshpass+ssh -L consegue criar port-forward e acessar HTTPS
# Uso: bash test_proxy_v4.sh <proxy_host> <proxy_port> <proxy_user> <proxy_pass> <target_host> <target_port> <scheme>
# Exemplo: bash test_proxy_v4.sh 200.1.2.3 22 admin secret 10.10.0.14 443 https

PROXY_HOST="${1:?Uso: $0 proxy_host proxy_port proxy_user proxy_pass target_host target_port scheme}"
PROXY_PORT="${2:?}"
PROXY_USER="${3:?}"
PROXY_PASS="${4:?}"
TARGET_HOST="${5:?}"
TARGET_PORT="${6:-443}"
SCHEME="${7:-https}"

# Encontrar porta livre
LOCAL_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")
echo "═══ Teste V4: $SCHEME://$TARGET_HOST:$TARGET_PORT via $PROXY_USER@$PROXY_HOST:$PROXY_PORT ═══"
echo "    Porta local: $LOCAL_PORT"
echo ""

# 1. Criar túnel SSH
echo "[1] Criando túnel SSH..."
sshpass -p "$PROXY_PASS" ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout=10 \
  -o LogLevel=ERROR \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o 'KexAlgorithms=+diffie-hellman-group-exchange-sha1,diffie-hellman-group14-sha1,diffie-hellman-group1-sha1' \
  -o 'HostKeyAlgorithms=+ssh-rsa,ssh-dss' \
  -N \
  -L "127.0.0.1:${LOCAL_PORT}:${TARGET_HOST}:${TARGET_PORT}" \
  -p "$PROXY_PORT" \
  "${PROXY_USER}@${PROXY_HOST}" &

SSH_PID=$!
echo "    PID: $SSH_PID"

# Aguardar túnel
echo "[2] Aguardando túnel ficar pronto..."
for i in $(seq 1 20); do
  sleep 0.3
  if ! kill -0 $SSH_PID 2>/dev/null; then
    echo "    ❌ ssh morreu!"
    wait $SSH_PID 2>/dev/null
    exit 1
  fi
  if timeout 1 bash -c "echo > /dev/tcp/127.0.0.1/$LOCAL_PORT" 2>/dev/null; then
    echo "    ✅ Túnel ativo"
    break
  fi
  if [ $i -eq 20 ]; then
    echo "    ❌ Túnel não ficou pronto"
    kill $SSH_PID 2>/dev/null
    exit 1
  fi
done
echo ""

# 3. Testar requisição
echo "[3] Testando requisição ${SCHEME}..."
echo "    URL: ${SCHEME}://127.0.0.1:${LOCAL_PORT}/"
echo ""
curl -vk --max-time 15 \
  -H "Host: ${TARGET_HOST}" \
  -H "User-Agent: Mozilla/5.0" \
  -o /dev/null -w "    Status: %{http_code}\n    Size: %{size_download} bytes\n    Redirect: %{redirect_url}\n    Time: %{time_total}s\n" \
  "${SCHEME}://127.0.0.1:${LOCAL_PORT}/" 2>&1 | grep -E "(< HTTP|< Location|< Content-Type|Status:|Size:|Redirect:|Time:|SSL|TLS|error)"

echo ""

# 4. Seguir redirects
echo "[4] Seguindo redirects (-L)..."
curl -kL --max-time 15 --max-redirs 5 \
  -H "Host: ${TARGET_HOST}" \
  -H "User-Agent: Mozilla/5.0" \
  -o /dev/null -w "    Final Status: %{http_code}\n    Final Size: %{size_download} bytes\n    Redirects: %{num_redirects}\n    Total Time: %{time_total}s\n" \
  "${SCHEME}://127.0.0.1:${LOCAL_PORT}/" 2>/dev/null

echo ""

# Cleanup
kill $SSH_PID 2>/dev/null
wait $SSH_PID 2>/dev/null
echo "═══ Teste concluído ═══"
