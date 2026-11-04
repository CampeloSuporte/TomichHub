#!/usr/bin/env python3
"""Instala o cliente UNM2000 da versão de um servidor específico.

O cliente precisa ser da mesma versão do servidor. Com versão diferente a RPC
GetDeviceCapability não decodifica (EN_BUS_DISPATCH_DECODE_RPC_REQUEST no
unm_ui.log), o CapabilityBean da OLT vem zerado e o NE Manager manda comandos
que a OLT não conhece ("[Device Return Error]Unkown Command").

Os patches Linux do port são bsdiff dos jars exatos da versão instalada em
/opt/unm2000/app, então não servem para outra versão. Este script pega os jars
do instalador do servidor e transplanta só as classes que os patches mudaram,
desde que elas sejam idênticas nas duas versões (confere e aborta se não forem).

Uso (como root):
  tools/unm2000_cliente_servidor.py <ip-servidor> <instalador-do-servidor.exe> <instalador-da-versao-atual.exe>

O instalador de cada servidor sai de http://<ip>:8080/webstart/unm2000_Client_en.exe
(pelo proxy do cliente). Resultado em /opt/unm2000/servidores/<ip>/app, que o
clientes/unm_vnc.py usa automaticamente para esse host.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ATUAL = "/opt/unm2000/app"
DESTINO = "/opt/unm2000/servidores"


def extrair(exe, pasta):
    subprocess.run(["unrar", "x", "-o+", "-inul", exe, "unm2000/*", pasta + "/"], check=False)
    raiz = os.path.join(pasta, "unm2000")
    if not os.path.isdir(raiz):
        sys.exit(f"extração falhou: {exe}")
    return raiz


def entradas(jar):
    with zipfile.ZipFile(jar) as z:
        return {n: z.read(n) for n in z.namelist() if not n.endswith("/")}


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    ip, exe_servidor, exe_atual = sys.argv[1:]
    ip = os.path.basename(ip.strip())
    dest = os.path.join(DESTINO, ip, "app")
    with tempfile.TemporaryDirectory() as tmp:
        novo = extrair(exe_servidor, os.path.join(tmp, "novo"))
        orig = extrair(exe_atual, os.path.join(tmp, "orig"))
        # O que o instalador do port mudou em relação ao pacote original
        mudados = []
        for dirpath, _, arquivos in os.walk(ATUAL):
            for nome in arquivos:
                cam = os.path.join(dirpath, nome)
                rel = os.path.relpath(cam, ATUAL)
                o = os.path.join(orig, rel)
                if not os.path.exists(o) or open(o, "rb").read() != open(cam, "rb").read():
                    mudados.append(rel)

        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(novo, dest, symlinks=True)

        for rel in mudados:
            alvo, o, n = os.path.join(dest, rel), os.path.join(orig, rel), os.path.join(novo, rel)
            if not rel.endswith(".jar") or not os.path.exists(o) or not os.path.exists(n) \
                    or open(o, "rb").read() == open(n, "rb").read():
                # Arquivo novo do port, conf, ou jar igual nas duas versões: copia direto
                os.makedirs(os.path.dirname(alvo), exist_ok=True)
                shutil.copy2(os.path.join(ATUAL, rel), alvo)
                print(f"copiado      {rel}")
                continue
            eo, ep, en = entradas(o), entradas(os.path.join(ATUAL, rel)), entradas(n)
            troca = {k: v for k, v in ep.items() if eo.get(k) != v}
            conflito = [k for k in troca if k in eo and en.get(k) != eo[k]]
            if conflito:
                sys.exit(f"{rel}: classes do patch mudaram na versão nova: {conflito}")
            with zipfile.ZipFile(n) as zn, zipfile.ZipFile(alvo, "w", zipfile.ZIP_DEFLATED) as w:
                for info in zn.infolist():
                    w.writestr(info, troca.pop(info.filename, None) or zn.read(info.filename))
                for k, v in troca.items():
                    w.writestr(k, v)
            print(f"transplante  {rel}")

    subprocess.run(["chmod", "-R", "a+rX", os.path.join(DESTINO, ip)], check=True)
    os.chmod(os.path.join(dest, "bin", "unm2000access"), 0o755)
    print(f"ok: {dest} ({hashlib.md5(open(exe_servidor, 'rb').read()).hexdigest()})")


if __name__ == "__main__":
    main()
