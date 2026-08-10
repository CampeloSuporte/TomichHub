//! Teste de fumaça manual: garante que o link com a lib `ampscan` funciona e
//! que a serialização JSON do resultado é sã, usando só loopback (sem tocar
//! rede externa). Exercita o mesmo caminho (`scanner::run_scan` com prefixo
//! CIDR) que o runner de produção usa — não `scan_single_ip`, que tem um bug
//! upstream: monta um Prefix com o IP puro (sem `/CIDR`), que falha ao ser
//! reparseado como IpNet dentro de `run_scan`.
use ampscan::db::models::{Port, Prefix};
use ampscan::scanner::{self, ScanConfig};
use std::time::Duration;

#[tokio::test]
async fn scans_loopback_closed_udp_port() {
    let port = Port {
        id: 1,
        port: 40000,
        protocol: "udp".to_string(),
        name: "TEST".to_string(),
        description: "porta de teste, deve aparecer fechada/inconclusiva".to_string(),
        probe_type: "udp_payload".to_string(),
        probe_payload: Some(vec![0x00]),
        enabled: true,
        created_at: String::new(),
        updated_at: String::new(),
    };

    let config = ScanConfig {
        concurrency: 4,
        timeout: Duration::from_millis(500),
        retries: 0,
    };

    let prefix = Prefix {
        id: 1,
        prefix: "127.0.0.1/32".to_string(),
        description: "loopback smoke test".to_string(),
        ip_version: 4,
        enabled: true,
        created_at: String::new(),
        updated_at: String::new(),
    };

    let report = scanner::run_scan(vec![port], vec![prefix], &config)
        .await
        .expect("run_scan failed");

    assert_eq!(report.results.len(), 1);
    let json = serde_json::to_string_pretty(&report).unwrap();
    println!("{}", json);
    assert!(json.contains("\"port\": 40000"));
}
