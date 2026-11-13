#!/usr/bin/env python3
"""Vigia de memória do container do CRM (crm-memoria-guard.service).

Por que existe: em 13/11/2026 a memória do LXC foi de 3,8 GB para 96 % em menos
de 14 minutos e o container travou (thrashing) por ~37 min até ser reiniciado à
mão. O OOM killer não agiu a tempo e não ficou registro de qual processo
cresceu. Este vigia faz duas coisas, só com a biblioteca padrão:

1. Registro: com a memória disponível abaixo de ALERTA_PCT, grava a cada
   INTERVALO_REGISTRO segundos os maiores processos (RSS, serviço systemd,
   comando) e o consumo de cada serviço em /var/log/crm-memoria.log.
2. Corte: com a disponível abaixo de CRITICO_PCT em 2 leituras seguidas, mata
   (SIGKILL) o maior processo que não esteja na lista de protegidos — antes do
   container entrar em thrashing. O serviço dono é reiniciado pelo systemd.

Os limites por serviço (MemoryHigh/MemoryMax nos drop-ins desta pasta) são a
primeira barreira; este vigia cobre o que estiver fora deles (shell, sessões,
scripts avulsos) ou um cenário em que a soma passe do total.
"""
import ctypes
import os
import signal
import sys
import time

INTERVALO = 2               # s entre leituras
ALERTA_PCT = float(os.environ.get('CRM_MEM_ALERTA_PCT', 25))
CRITICO_PCT = float(os.environ.get('CRM_MEM_CRITICO_PCT', 6))
INTERVALO_REGISTRO = 30     # s entre registros enquanto em alerta
PAUSA_APOS_CORTE = 10       # s para o kernel devolver a memória
LOG = os.environ.get('CRM_MEM_LOG', '/var/log/crm-memoria.log')

# Nunca matar: banco, infraestrutura do container e túneis dos clientes.
PROTEGIDOS = {
    'systemd', 'systemd-journal', 'systemd-journald', 'systemd-logind',
    'systemd-udevd', 'systemd-network', 'systemd-resolve', 'init',
    'postgres', 'redis-server', 'nginx', 'sshd', 'sshd-session', 'openvpn',
    'dbus-daemon', 'rsyslogd', 'cron', 'agetty', 'fail2ban-server',
}
PAGINA = os.sysconf('SC_PAGE_SIZE')
MEU_PID = os.getpid()


def meminfo():
    dados = {}
    with open('/proc/meminfo') as f:
        for linha in f:
            nome, valor = linha.split(':', 1)
            dados[nome] = int(valor.split()[0]) * 1024
    return dados['MemTotal'], dados.get('MemAvailable', dados['MemFree'])


def _ler(caminho, padrao=''):
    try:
        with open(caminho) as f:
            return f.read()
    except OSError:
        return padrao


def processos():
    """[(rss_bytes, pid, nome, servico, cmdline)] ordenado do maior p/ o menor."""
    lista = []
    for nome in os.listdir('/proc'):
        if not nome.isdigit():
            continue
        pid = int(nome)
        statm = _ler(f'/proc/{pid}/statm')
        if not statm:
            continue
        rss = int(statm.split()[1]) * PAGINA
        if rss == 0:          # threads de kernel
            continue
        comm = _ler(f'/proc/{pid}/comm').strip()
        cg = _ler(f'/proc/{pid}/cgroup').strip().rsplit('/', 1)[-1]
        cmd = _ler(f'/proc/{pid}/cmdline').replace('\0', ' ').strip()
        lista.append((rss, pid, comm, cg, cmd))
    lista.sort(reverse=True)
    return lista


def servicos():
    base = '/sys/fs/cgroup/system.slice'
    saida = []
    try:
        nomes = os.listdir(base)
    except OSError:
        return saida
    for nome in nomes:
        atual = _ler(f'{base}/{nome}/memory.current').strip()
        if atual.isdigit() and int(atual) > 50 * 1024 * 1024:
            saida.append((int(atual), nome))
    saida.sort(reverse=True)
    return saida


def mb(n):
    return f'{n / 1048576:.0f}MB'


def registrar(texto):
    carimbo = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(LOG, 'a') as f:
            f.write(f'[{carimbo}] {texto}\n')
    except OSError:
        pass
    print(texto, flush=True)   # journal


def retrato(total, disp, procs, motivo):
    linhas = [f'{motivo}: disponível {mb(disp)} de {mb(total)} '
              f'({100 * disp / total:.1f}%)']
    for rss, pid, comm, cg, cmd in procs[:15]:
        linhas.append(f'    {mb(rss):>8} pid={pid} {cg or "-"} {comm}: {cmd[:160]}')
    linhas.append('    serviços: ' + ', '.join(f'{n}={mb(v)}' for v, n in servicos()[:10]))
    registrar('\n'.join(linhas))


def escolher_vitima(procs):
    for rss, pid, comm, cg, cmd in procs:
        if pid in (1, MEU_PID) or comm in PROTEGIDOS:
            continue
        if cg.startswith('postgresql') or cg.startswith('openvpn'):
            continue
        return rss, pid, comm, cg, cmd
    return None


def travar_na_ram():
    """mlockall: o vigia não pode ir para o swap justo quando precisa agir."""
    try:
        libc = ctypes.CDLL('libc.so.6', use_errno=True)
        libc.mlockall(3)   # MCL_CURRENT | MCL_FUTURE
    except Exception:
        pass


def main():
    travar_na_ram()
    registrar(f'crm-memoria-guard iniciado: alerta<{ALERTA_PCT}% crítico<{CRITICO_PCT}%')
    ultimo_registro = 0.0
    criticos_seguidos = 0
    while True:
        try:
            total, disp = meminfo()
            pct = 100 * disp / total
            agora = time.monotonic()
            if pct < CRITICO_PCT:
                criticos_seguidos += 1
            else:
                criticos_seguidos = 0

            if criticos_seguidos >= 2:
                procs = processos()
                retrato(total, disp, procs, 'CRÍTICO')
                vitima = escolher_vitima(procs)
                if vitima:
                    rss, pid, comm, cg, cmd = vitima
                    try:
                        os.kill(pid, signal.SIGKILL)
                        registrar(f'CORTE: SIGKILL pid={pid} {cg} {comm} ({mb(rss)}): {cmd[:200]}')
                    except ProcessLookupError:
                        pass
                    except PermissionError as e:
                        registrar(f'CORTE falhou pid={pid}: {e}')
                criticos_seguidos = 0
                ultimo_registro = agora
                time.sleep(PAUSA_APOS_CORTE)
                continue

            if pct < ALERTA_PCT and agora - ultimo_registro >= INTERVALO_REGISTRO:
                retrato(total, disp, processos(), 'ALERTA')
                ultimo_registro = agora
        except Exception as e:   # o vigia nunca pode morrer por um erro bobo
            print(f'erro no vigia: {e}', file=sys.stderr, flush=True)
        time.sleep(INTERVALO)


if __name__ == '__main__':
    main()
