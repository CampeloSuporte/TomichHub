//! Runner fino em torno da lib `ampscan` (https://github.com/gondimcodes/ampscan).
//!
//! Não usa o banco SQLCipher nem a autenticação de usuário do CLI original —
//! lê a lista de prefixos a escanear via stdin (JSON), monta a mesma lista de
//! portas padrão que o `ampscan init` semeia no banco, chama diretamente
//! `ampscan::scanner::run_scan` e imprime o `ScanReport` resultante como JSON
//! em stdout. Isso deixa o resultado 100% estruturado para o Celery consumir,
//! sem precisar parsear a tabela colorida que o CLI original imprime (o CLI
//! não tem flag --json).

use ampscan::db::models::{Port, Prefix};
use ampscan::scanner::{self, ScanConfig};
use anyhow::{Context, Result};
use serde::Deserialize;
use std::io::Read;
use std::time::Duration;

#[derive(Deserialize)]
struct PrefixInput {
    /// Id do BlocoIP no CRM — devolvido em prefixes_scanned só como referência;
    /// o mapeamento IP -> BlocoIP é feito no lado Python por contenção de CIDR.
    #[serde(default)]
    id: i64,
    prefix: String,
    #[serde(default)]
    description: String,
}

#[derive(Deserialize)]
struct RunnerInput {
    prefixes: Vec<PrefixInput>,
    #[serde(default = "default_concurrency")]
    concurrency: usize,
    #[serde(default = "default_timeout")]
    timeout: u64,
    #[serde(default = "default_retries")]
    retries: usize,
}

fn default_concurrency() -> usize {
    300
}
fn default_timeout() -> u64 {
    2
}
fn default_retries() -> usize {
    1
}

/// Réplica exata da lista semeada por `ampscan::db::port_repo::seed_default_ports`
/// (mesmos 21 pares porta/protocolo/probe da versão pinada no Cargo.toml).
fn default_ports() -> Vec<Port> {
    let defaults: Vec<(u16, &str, &str, &str, &str, Vec<u8>)> = vec![
        (17, "udp", "QOTD", "Quote of the Day - legacy service that responds to any packet, used for DDoS amplification", "udp_payload", vec![]),
        (19, "udp", "CHARGEN", "Character Generator - legacy service that generates a character stream, used for DDoS amplification", "udp_payload", vec![]),
        (53, "udp", "DNS", "Domain Name System - open resolvers can amplify traffic up to 54x", "dns", vec![]),
        (69, "udp", "TFTP", "Trivial File Transfer Protocol - exposed TFTP servers allow file extraction and amplification", "tftp", vec![]),
        (111, "udp", "RPC", "Remote Procedure Call Portmapper - exposure allows service enumeration and amplification", "rpc", vec![]),
        (123, "udp", "NTP", "Network Time Protocol - servers with monlist/readvar amplify traffic up to 556x", "ntp", vec![]),
        (137, "udp", "NETBIOS", "NetBIOS Name Service - exposure reveals network information and allows amplification", "netbios", vec![]),
        (520, "udp", "RIPv1", "Routing Information Protocol v1 - legacy routing daemon that amplifies traffic up to 30x", "ripv1", vec![]),
        (161, "udp", "SNMP", "Simple Network Management Protocol - 'public' community exposes data and amplifies up to 6.3x", "snmp", vec![]),
        (389, "udp", "LDAP", "CLDAP - Connectionless LDAP used for massive amplification up to 70x", "ldap", vec![]),
        (427, "udp", "SLP", "Service Location Protocol - used for service amplification up to 2200x", "udp_payload", vec![
            0x02, 0x01, 0x00, 0x00, 0x36, 0x20, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00,
            0x02, 0x65, 0x6e, 0x00, 0x00, 0x00, 0x15, 0x73, 0x65, 0x72, 0x76, 0x69, 0x63,
            0x65, 0x3a, 0x73, 0x65, 0x72, 0x76, 0x69, 0x63, 0x65, 0x2d, 0x61, 0x67, 0x65,
            0x6e, 0x74, 0x00, 0x07, 0x64, 0x65, 0x66, 0x61, 0x75, 0x6c, 0x74, 0x00, 0x00,
            0x00, 0x00,
        ]),
        (1900, "udp", "SSDP", "Simple Service Discovery Protocol - UPnP amplifies traffic up to 30x", "ssdp", vec![]),
        (3283, "udp", "ARMS", "Apple Remote Management Service - amplification via Apple Remote Desktop", "udp_payload", vec![0x00, 0x14, 0x00, 0x01, 0x03]),
        (3702, "udp", "WS-DISCOVERY", "Web Services Dynamic Discovery - SOAP/XML amplification up to 153x", "udp_payload", vec![0x3C, 0xAA, 0x3E, 0x0A]),
        (5353, "udp", "mDNS", "Multicast DNS - open mDNS resolvers amplify up to 4.7x", "mdns", vec![]),
        (5683, "udp", "CoAP", "Constrained Application Protocol - IoT devices amplify up to 34x", "udp_payload", vec![
            0x40, 0x01, 0x7d, 0x70, 0xbb, 0x2e, 0x77, 0x65, 0x6c, 0x6c, 0x2d, 0x6b, 0x6e,
            0x6f, 0x77, 0x6e, 0x04, 0x63, 0x6f, 0x72, 0x65,
        ]),
        (10001, "udp", "UBNT", "Ubiquiti Discovery Protocol - exposed Ubiquiti devices on the network", "udp_payload", vec![0x01, 0x00, 0x00, 0x00]),
        (11211, "udp", "MEMCACHED", "Memcached - massive amplification up to 51000x, extremely dangerous", "memcached", vec![]),
        (37810, "udp", "DVR-DHCPDiscover", "DVR DHCP Discovery - exposed cameras and DVRs responding to discovery", "udp_payload", vec![0xFF]),
        (4145, "tcp", "MT4145", "MikroTik open SOCKS proxy - may indicate a compromised MikroTik device", "tcp_connect", vec![]),
        (5678, "tcp", "MT5678", "MikroTik Meris botnet - indicates possible Meris botnet infection (DDoS)", "tcp_connect", vec![]),
    ];

    let now = String::new();
    defaults
        .into_iter()
        .enumerate()
        .map(|(idx, (port, proto, name, desc, probe_type, payload))| Port {
            id: idx as i64 + 1,
            port,
            protocol: proto.to_string(),
            name: name.to_string(),
            description: desc.to_string(),
            probe_type: probe_type.to_string(),
            probe_payload: if payload.is_empty() { None } else { Some(payload) },
            enabled: true,
            created_at: now.clone(),
            updated_at: now.clone(),
        })
        .collect()
}

#[tokio::main]
async fn main() -> Result<()> {
    let mut raw = String::new();
    std::io::stdin()
        .read_to_string(&mut raw)
        .context("failed to read stdin")?;

    let parsed: RunnerInput =
        serde_json::from_str(&raw).context("invalid JSON input on stdin")?;

    if parsed.prefixes.is_empty() {
        anyhow::bail!("no prefixes provided");
    }

    let prefixes: Vec<Prefix> = parsed
        .prefixes
        .into_iter()
        .map(|p| {
            let ip_version: u8 = if p.prefix.contains(':') { 6 } else { 4 };
            Prefix {
                id: p.id,
                prefix: p.prefix,
                description: p.description,
                ip_version,
                enabled: true,
                created_at: String::new(),
                updated_at: String::new(),
            }
        })
        .collect();

    let config = ScanConfig {
        concurrency: parsed.concurrency,
        timeout: Duration::from_secs(parsed.timeout),
        retries: parsed.retries,
    };

    let report = scanner::run_scan(default_ports(), prefixes, &config).await?;

    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}
