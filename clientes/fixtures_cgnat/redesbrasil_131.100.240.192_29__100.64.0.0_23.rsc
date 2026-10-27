
#====== Script CGNAT by Redes Brasil ==========================
        
/ip firewall address-list add list=rede-privada-para-cgnat address=100.64.0.0/23
/ip firewall nat add action=jump chain=srcnat comment="Jump ==> CGNAT" jump-target=cgnat src-address-list=rede-privada-para-cgnat
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.0/29 portas=1024-2031" jump-target=100.64.0.0/29 src-address=100.64.0.0/29
/ip firewall nat add chain=100.64.0.0/29 comment="100.64.0.0/29 ==> portas=1024-2031" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=1024-2031
/ip firewall nat add chain=100.64.0.0/29 comment="100.64.0.0/29 ==> portas=1024-2031" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=1024-2031
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.8/29 portas=2032-3039" jump-target=100.64.0.8/29 src-address=100.64.0.8/29
/ip firewall nat add chain=100.64.0.8/29 comment="100.64.0.8/29 ==> portas=2032-3039" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=2032-3039
/ip firewall nat add chain=100.64.0.8/29 comment="100.64.0.8/29 ==> portas=2032-3039" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=2032-3039
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.16/29 portas=3040-4047" jump-target=100.64.0.16/29 src-address=100.64.0.16/29
/ip firewall nat add chain=100.64.0.16/29 comment="100.64.0.16/29 ==> portas=3040-4047" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=3040-4047
/ip firewall nat add chain=100.64.0.16/29 comment="100.64.0.16/29 ==> portas=3040-4047" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=3040-4047
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.24/29 portas=4048-5055" jump-target=100.64.0.24/29 src-address=100.64.0.24/29
/ip firewall nat add chain=100.64.0.24/29 comment="100.64.0.24/29 ==> portas=4048-5055" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=4048-5055
/ip firewall nat add chain=100.64.0.24/29 comment="100.64.0.24/29 ==> portas=4048-5055" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=4048-5055
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.32/29 portas=5056-6063" jump-target=100.64.0.32/29 src-address=100.64.0.32/29
/ip firewall nat add chain=100.64.0.32/29 comment="100.64.0.32/29 ==> portas=5056-6063" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=5056-6063
/ip firewall nat add chain=100.64.0.32/29 comment="100.64.0.32/29 ==> portas=5056-6063" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=5056-6063
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.40/29 portas=6064-7071" jump-target=100.64.0.40/29 src-address=100.64.0.40/29
/ip firewall nat add chain=100.64.0.40/29 comment="100.64.0.40/29 ==> portas=6064-7071" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=6064-7071
/ip firewall nat add chain=100.64.0.40/29 comment="100.64.0.40/29 ==> portas=6064-7071" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=6064-7071
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.48/29 portas=7072-8079" jump-target=100.64.0.48/29 src-address=100.64.0.48/29
/ip firewall nat add chain=100.64.0.48/29 comment="100.64.0.48/29 ==> portas=7072-8079" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=7072-8079
/ip firewall nat add chain=100.64.0.48/29 comment="100.64.0.48/29 ==> portas=7072-8079" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=7072-8079
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.56/29 portas=8080-9087" jump-target=100.64.0.56/29 src-address=100.64.0.56/29
/ip firewall nat add chain=100.64.0.56/29 comment="100.64.0.56/29 ==> portas=8080-9087" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=8080-9087
/ip firewall nat add chain=100.64.0.56/29 comment="100.64.0.56/29 ==> portas=8080-9087" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=8080-9087
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.64/29 portas=9088-10095" jump-target=100.64.0.64/29 src-address=100.64.0.64/29
/ip firewall nat add chain=100.64.0.64/29 comment="100.64.0.64/29 ==> portas=9088-10095" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=9088-10095
/ip firewall nat add chain=100.64.0.64/29 comment="100.64.0.64/29 ==> portas=9088-10095" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=9088-10095
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.72/29 portas=10096-11103" jump-target=100.64.0.72/29 src-address=100.64.0.72/29
/ip firewall nat add chain=100.64.0.72/29 comment="100.64.0.72/29 ==> portas=10096-11103" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=10096-11103
/ip firewall nat add chain=100.64.0.72/29 comment="100.64.0.72/29 ==> portas=10096-11103" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=10096-11103
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.80/29 portas=11104-12111" jump-target=100.64.0.80/29 src-address=100.64.0.80/29
/ip firewall nat add chain=100.64.0.80/29 comment="100.64.0.80/29 ==> portas=11104-12111" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=11104-12111
/ip firewall nat add chain=100.64.0.80/29 comment="100.64.0.80/29 ==> portas=11104-12111" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=11104-12111
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.88/29 portas=12112-13119" jump-target=100.64.0.88/29 src-address=100.64.0.88/29
/ip firewall nat add chain=100.64.0.88/29 comment="100.64.0.88/29 ==> portas=12112-13119" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=12112-13119
/ip firewall nat add chain=100.64.0.88/29 comment="100.64.0.88/29 ==> portas=12112-13119" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=12112-13119
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.96/29 portas=13120-14127" jump-target=100.64.0.96/29 src-address=100.64.0.96/29
/ip firewall nat add chain=100.64.0.96/29 comment="100.64.0.96/29 ==> portas=13120-14127" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=13120-14127
/ip firewall nat add chain=100.64.0.96/29 comment="100.64.0.96/29 ==> portas=13120-14127" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=13120-14127
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.104/29 portas=14128-15135" jump-target=100.64.0.104/29 src-address=100.64.0.104/29
/ip firewall nat add chain=100.64.0.104/29 comment="100.64.0.104/29 ==> portas=14128-15135" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=14128-15135
/ip firewall nat add chain=100.64.0.104/29 comment="100.64.0.104/29 ==> portas=14128-15135" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=14128-15135
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.112/29 portas=15136-16143" jump-target=100.64.0.112/29 src-address=100.64.0.112/29
/ip firewall nat add chain=100.64.0.112/29 comment="100.64.0.112/29 ==> portas=15136-16143" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=15136-16143
/ip firewall nat add chain=100.64.0.112/29 comment="100.64.0.112/29 ==> portas=15136-16143" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=15136-16143
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.120/29 portas=16144-17151" jump-target=100.64.0.120/29 src-address=100.64.0.120/29
/ip firewall nat add chain=100.64.0.120/29 comment="100.64.0.120/29 ==> portas=16144-17151" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=16144-17151
/ip firewall nat add chain=100.64.0.120/29 comment="100.64.0.120/29 ==> portas=16144-17151" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=16144-17151
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.128/29 portas=17152-18159" jump-target=100.64.0.128/29 src-address=100.64.0.128/29
/ip firewall nat add chain=100.64.0.128/29 comment="100.64.0.128/29 ==> portas=17152-18159" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=17152-18159
/ip firewall nat add chain=100.64.0.128/29 comment="100.64.0.128/29 ==> portas=17152-18159" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=17152-18159
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.136/29 portas=18160-19167" jump-target=100.64.0.136/29 src-address=100.64.0.136/29
/ip firewall nat add chain=100.64.0.136/29 comment="100.64.0.136/29 ==> portas=18160-19167" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=18160-19167
/ip firewall nat add chain=100.64.0.136/29 comment="100.64.0.136/29 ==> portas=18160-19167" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=18160-19167
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.144/29 portas=19168-20175" jump-target=100.64.0.144/29 src-address=100.64.0.144/29
/ip firewall nat add chain=100.64.0.144/29 comment="100.64.0.144/29 ==> portas=19168-20175" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=19168-20175
/ip firewall nat add chain=100.64.0.144/29 comment="100.64.0.144/29 ==> portas=19168-20175" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=19168-20175
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.152/29 portas=20176-21183" jump-target=100.64.0.152/29 src-address=100.64.0.152/29
/ip firewall nat add chain=100.64.0.152/29 comment="100.64.0.152/29 ==> portas=20176-21183" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=20176-21183
/ip firewall nat add chain=100.64.0.152/29 comment="100.64.0.152/29 ==> portas=20176-21183" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=20176-21183
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.160/29 portas=21184-22191" jump-target=100.64.0.160/29 src-address=100.64.0.160/29
/ip firewall nat add chain=100.64.0.160/29 comment="100.64.0.160/29 ==> portas=21184-22191" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=21184-22191
/ip firewall nat add chain=100.64.0.160/29 comment="100.64.0.160/29 ==> portas=21184-22191" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=21184-22191
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.168/29 portas=22192-23199" jump-target=100.64.0.168/29 src-address=100.64.0.168/29
/ip firewall nat add chain=100.64.0.168/29 comment="100.64.0.168/29 ==> portas=22192-23199" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=22192-23199
/ip firewall nat add chain=100.64.0.168/29 comment="100.64.0.168/29 ==> portas=22192-23199" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=22192-23199
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.176/29 portas=23200-24207" jump-target=100.64.0.176/29 src-address=100.64.0.176/29
/ip firewall nat add chain=100.64.0.176/29 comment="100.64.0.176/29 ==> portas=23200-24207" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=23200-24207
/ip firewall nat add chain=100.64.0.176/29 comment="100.64.0.176/29 ==> portas=23200-24207" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=23200-24207
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.184/29 portas=24208-25215" jump-target=100.64.0.184/29 src-address=100.64.0.184/29
/ip firewall nat add chain=100.64.0.184/29 comment="100.64.0.184/29 ==> portas=24208-25215" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=24208-25215
/ip firewall nat add chain=100.64.0.184/29 comment="100.64.0.184/29 ==> portas=24208-25215" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=24208-25215
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.192/29 portas=25216-26223" jump-target=100.64.0.192/29 src-address=100.64.0.192/29
/ip firewall nat add chain=100.64.0.192/29 comment="100.64.0.192/29 ==> portas=25216-26223" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=25216-26223
/ip firewall nat add chain=100.64.0.192/29 comment="100.64.0.192/29 ==> portas=25216-26223" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=25216-26223
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.200/29 portas=26224-27231" jump-target=100.64.0.200/29 src-address=100.64.0.200/29
/ip firewall nat add chain=100.64.0.200/29 comment="100.64.0.200/29 ==> portas=26224-27231" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=26224-27231
/ip firewall nat add chain=100.64.0.200/29 comment="100.64.0.200/29 ==> portas=26224-27231" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=26224-27231
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.208/29 portas=27232-28239" jump-target=100.64.0.208/29 src-address=100.64.0.208/29
/ip firewall nat add chain=100.64.0.208/29 comment="100.64.0.208/29 ==> portas=27232-28239" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=27232-28239
/ip firewall nat add chain=100.64.0.208/29 comment="100.64.0.208/29 ==> portas=27232-28239" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=27232-28239
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.216/29 portas=28240-29247" jump-target=100.64.0.216/29 src-address=100.64.0.216/29
/ip firewall nat add chain=100.64.0.216/29 comment="100.64.0.216/29 ==> portas=28240-29247" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=28240-29247
/ip firewall nat add chain=100.64.0.216/29 comment="100.64.0.216/29 ==> portas=28240-29247" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=28240-29247
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.224/29 portas=29248-30255" jump-target=100.64.0.224/29 src-address=100.64.0.224/29
/ip firewall nat add chain=100.64.0.224/29 comment="100.64.0.224/29 ==> portas=29248-30255" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=29248-30255
/ip firewall nat add chain=100.64.0.224/29 comment="100.64.0.224/29 ==> portas=29248-30255" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=29248-30255
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.232/29 portas=30256-31263" jump-target=100.64.0.232/29 src-address=100.64.0.232/29
/ip firewall nat add chain=100.64.0.232/29 comment="100.64.0.232/29 ==> portas=30256-31263" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=30256-31263
/ip firewall nat add chain=100.64.0.232/29 comment="100.64.0.232/29 ==> portas=30256-31263" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=30256-31263
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.240/29 portas=31264-32271" jump-target=100.64.0.240/29 src-address=100.64.0.240/29
/ip firewall nat add chain=100.64.0.240/29 comment="100.64.0.240/29 ==> portas=31264-32271" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=31264-32271
/ip firewall nat add chain=100.64.0.240/29 comment="100.64.0.240/29 ==> portas=31264-32271" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=31264-32271
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.0.248/29 portas=32272-33279" jump-target=100.64.0.248/29 src-address=100.64.0.248/29
/ip firewall nat add chain=100.64.0.248/29 comment="100.64.0.248/29 ==> portas=32272-33279" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=32272-33279
/ip firewall nat add chain=100.64.0.248/29 comment="100.64.0.248/29 ==> portas=32272-33279" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=32272-33279
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.0/29 portas=33280-34287" jump-target=100.64.1.0/29 src-address=100.64.1.0/29
/ip firewall nat add chain=100.64.1.0/29 comment="100.64.1.0/29 ==> portas=33280-34287" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=33280-34287
/ip firewall nat add chain=100.64.1.0/29 comment="100.64.1.0/29 ==> portas=33280-34287" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=33280-34287
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.8/29 portas=34288-35295" jump-target=100.64.1.8/29 src-address=100.64.1.8/29
/ip firewall nat add chain=100.64.1.8/29 comment="100.64.1.8/29 ==> portas=34288-35295" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=34288-35295
/ip firewall nat add chain=100.64.1.8/29 comment="100.64.1.8/29 ==> portas=34288-35295" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=34288-35295
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.16/29 portas=35296-36303" jump-target=100.64.1.16/29 src-address=100.64.1.16/29
/ip firewall nat add chain=100.64.1.16/29 comment="100.64.1.16/29 ==> portas=35296-36303" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=35296-36303
/ip firewall nat add chain=100.64.1.16/29 comment="100.64.1.16/29 ==> portas=35296-36303" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=35296-36303
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.24/29 portas=36304-37311" jump-target=100.64.1.24/29 src-address=100.64.1.24/29
/ip firewall nat add chain=100.64.1.24/29 comment="100.64.1.24/29 ==> portas=36304-37311" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=36304-37311
/ip firewall nat add chain=100.64.1.24/29 comment="100.64.1.24/29 ==> portas=36304-37311" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=36304-37311
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.32/29 portas=37312-38319" jump-target=100.64.1.32/29 src-address=100.64.1.32/29
/ip firewall nat add chain=100.64.1.32/29 comment="100.64.1.32/29 ==> portas=37312-38319" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=37312-38319
/ip firewall nat add chain=100.64.1.32/29 comment="100.64.1.32/29 ==> portas=37312-38319" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=37312-38319
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.40/29 portas=38320-39327" jump-target=100.64.1.40/29 src-address=100.64.1.40/29
/ip firewall nat add chain=100.64.1.40/29 comment="100.64.1.40/29 ==> portas=38320-39327" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=38320-39327
/ip firewall nat add chain=100.64.1.40/29 comment="100.64.1.40/29 ==> portas=38320-39327" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=38320-39327
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.48/29 portas=39328-40335" jump-target=100.64.1.48/29 src-address=100.64.1.48/29
/ip firewall nat add chain=100.64.1.48/29 comment="100.64.1.48/29 ==> portas=39328-40335" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=39328-40335
/ip firewall nat add chain=100.64.1.48/29 comment="100.64.1.48/29 ==> portas=39328-40335" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=39328-40335
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.56/29 portas=40336-41343" jump-target=100.64.1.56/29 src-address=100.64.1.56/29
/ip firewall nat add chain=100.64.1.56/29 comment="100.64.1.56/29 ==> portas=40336-41343" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=40336-41343
/ip firewall nat add chain=100.64.1.56/29 comment="100.64.1.56/29 ==> portas=40336-41343" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=40336-41343
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.64/29 portas=41344-42351" jump-target=100.64.1.64/29 src-address=100.64.1.64/29
/ip firewall nat add chain=100.64.1.64/29 comment="100.64.1.64/29 ==> portas=41344-42351" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=41344-42351
/ip firewall nat add chain=100.64.1.64/29 comment="100.64.1.64/29 ==> portas=41344-42351" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=41344-42351
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.72/29 portas=42352-43359" jump-target=100.64.1.72/29 src-address=100.64.1.72/29
/ip firewall nat add chain=100.64.1.72/29 comment="100.64.1.72/29 ==> portas=42352-43359" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=42352-43359
/ip firewall nat add chain=100.64.1.72/29 comment="100.64.1.72/29 ==> portas=42352-43359" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=42352-43359
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.80/29 portas=43360-44367" jump-target=100.64.1.80/29 src-address=100.64.1.80/29
/ip firewall nat add chain=100.64.1.80/29 comment="100.64.1.80/29 ==> portas=43360-44367" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=43360-44367
/ip firewall nat add chain=100.64.1.80/29 comment="100.64.1.80/29 ==> portas=43360-44367" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=43360-44367
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.88/29 portas=44368-45375" jump-target=100.64.1.88/29 src-address=100.64.1.88/29
/ip firewall nat add chain=100.64.1.88/29 comment="100.64.1.88/29 ==> portas=44368-45375" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=44368-45375
/ip firewall nat add chain=100.64.1.88/29 comment="100.64.1.88/29 ==> portas=44368-45375" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=44368-45375
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.96/29 portas=45376-46383" jump-target=100.64.1.96/29 src-address=100.64.1.96/29
/ip firewall nat add chain=100.64.1.96/29 comment="100.64.1.96/29 ==> portas=45376-46383" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=45376-46383
/ip firewall nat add chain=100.64.1.96/29 comment="100.64.1.96/29 ==> portas=45376-46383" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=45376-46383
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.104/29 portas=46384-47391" jump-target=100.64.1.104/29 src-address=100.64.1.104/29
/ip firewall nat add chain=100.64.1.104/29 comment="100.64.1.104/29 ==> portas=46384-47391" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=46384-47391
/ip firewall nat add chain=100.64.1.104/29 comment="100.64.1.104/29 ==> portas=46384-47391" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=46384-47391
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.112/29 portas=47392-48399" jump-target=100.64.1.112/29 src-address=100.64.1.112/29
/ip firewall nat add chain=100.64.1.112/29 comment="100.64.1.112/29 ==> portas=47392-48399" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=47392-48399
/ip firewall nat add chain=100.64.1.112/29 comment="100.64.1.112/29 ==> portas=47392-48399" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=47392-48399
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.120/29 portas=48400-49407" jump-target=100.64.1.120/29 src-address=100.64.1.120/29
/ip firewall nat add chain=100.64.1.120/29 comment="100.64.1.120/29 ==> portas=48400-49407" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=48400-49407
/ip firewall nat add chain=100.64.1.120/29 comment="100.64.1.120/29 ==> portas=48400-49407" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=48400-49407
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.128/29 portas=49408-50415" jump-target=100.64.1.128/29 src-address=100.64.1.128/29
/ip firewall nat add chain=100.64.1.128/29 comment="100.64.1.128/29 ==> portas=49408-50415" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=49408-50415
/ip firewall nat add chain=100.64.1.128/29 comment="100.64.1.128/29 ==> portas=49408-50415" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=49408-50415
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.136/29 portas=50416-51423" jump-target=100.64.1.136/29 src-address=100.64.1.136/29
/ip firewall nat add chain=100.64.1.136/29 comment="100.64.1.136/29 ==> portas=50416-51423" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=50416-51423
/ip firewall nat add chain=100.64.1.136/29 comment="100.64.1.136/29 ==> portas=50416-51423" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=50416-51423
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.144/29 portas=51424-52431" jump-target=100.64.1.144/29 src-address=100.64.1.144/29
/ip firewall nat add chain=100.64.1.144/29 comment="100.64.1.144/29 ==> portas=51424-52431" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=51424-52431
/ip firewall nat add chain=100.64.1.144/29 comment="100.64.1.144/29 ==> portas=51424-52431" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=51424-52431
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.152/29 portas=52432-53439" jump-target=100.64.1.152/29 src-address=100.64.1.152/29
/ip firewall nat add chain=100.64.1.152/29 comment="100.64.1.152/29 ==> portas=52432-53439" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=52432-53439
/ip firewall nat add chain=100.64.1.152/29 comment="100.64.1.152/29 ==> portas=52432-53439" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=52432-53439
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.160/29 portas=53440-54447" jump-target=100.64.1.160/29 src-address=100.64.1.160/29
/ip firewall nat add chain=100.64.1.160/29 comment="100.64.1.160/29 ==> portas=53440-54447" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=53440-54447
/ip firewall nat add chain=100.64.1.160/29 comment="100.64.1.160/29 ==> portas=53440-54447" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=53440-54447
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.168/29 portas=54448-55455" jump-target=100.64.1.168/29 src-address=100.64.1.168/29
/ip firewall nat add chain=100.64.1.168/29 comment="100.64.1.168/29 ==> portas=54448-55455" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=54448-55455
/ip firewall nat add chain=100.64.1.168/29 comment="100.64.1.168/29 ==> portas=54448-55455" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=54448-55455
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.176/29 portas=55456-56463" jump-target=100.64.1.176/29 src-address=100.64.1.176/29
/ip firewall nat add chain=100.64.1.176/29 comment="100.64.1.176/29 ==> portas=55456-56463" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=55456-56463
/ip firewall nat add chain=100.64.1.176/29 comment="100.64.1.176/29 ==> portas=55456-56463" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=55456-56463
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.184/29 portas=56464-57471" jump-target=100.64.1.184/29 src-address=100.64.1.184/29
/ip firewall nat add chain=100.64.1.184/29 comment="100.64.1.184/29 ==> portas=56464-57471" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=56464-57471
/ip firewall nat add chain=100.64.1.184/29 comment="100.64.1.184/29 ==> portas=56464-57471" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=56464-57471
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.192/29 portas=57472-58479" jump-target=100.64.1.192/29 src-address=100.64.1.192/29
/ip firewall nat add chain=100.64.1.192/29 comment="100.64.1.192/29 ==> portas=57472-58479" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=57472-58479
/ip firewall nat add chain=100.64.1.192/29 comment="100.64.1.192/29 ==> portas=57472-58479" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=57472-58479
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.200/29 portas=58480-59487" jump-target=100.64.1.200/29 src-address=100.64.1.200/29
/ip firewall nat add chain=100.64.1.200/29 comment="100.64.1.200/29 ==> portas=58480-59487" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=58480-59487
/ip firewall nat add chain=100.64.1.200/29 comment="100.64.1.200/29 ==> portas=58480-59487" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=58480-59487
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.208/29 portas=59488-60495" jump-target=100.64.1.208/29 src-address=100.64.1.208/29
/ip firewall nat add chain=100.64.1.208/29 comment="100.64.1.208/29 ==> portas=59488-60495" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=59488-60495
/ip firewall nat add chain=100.64.1.208/29 comment="100.64.1.208/29 ==> portas=59488-60495" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=59488-60495
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.216/29 portas=60496-61503" jump-target=100.64.1.216/29 src-address=100.64.1.216/29
/ip firewall nat add chain=100.64.1.216/29 comment="100.64.1.216/29 ==> portas=60496-61503" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=60496-61503
/ip firewall nat add chain=100.64.1.216/29 comment="100.64.1.216/29 ==> portas=60496-61503" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=60496-61503
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.224/29 portas=61504-62511" jump-target=100.64.1.224/29 src-address=100.64.1.224/29
/ip firewall nat add chain=100.64.1.224/29 comment="100.64.1.224/29 ==> portas=61504-62511" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=61504-62511
/ip firewall nat add chain=100.64.1.224/29 comment="100.64.1.224/29 ==> portas=61504-62511" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=61504-62511
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.232/29 portas=62512-63519" jump-target=100.64.1.232/29 src-address=100.64.1.232/29
/ip firewall nat add chain=100.64.1.232/29 comment="100.64.1.232/29 ==> portas=62512-63519" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=62512-63519
/ip firewall nat add chain=100.64.1.232/29 comment="100.64.1.232/29 ==> portas=62512-63519" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=62512-63519
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.240/29 portas=63520-64527" jump-target=100.64.1.240/29 src-address=100.64.1.240/29
/ip firewall nat add chain=100.64.1.240/29 comment="100.64.1.240/29 ==> portas=63520-64527" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=63520-64527
/ip firewall nat add chain=100.64.1.240/29 comment="100.64.1.240/29 ==> portas=63520-64527" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=63520-64527
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.64.1.248/29 portas=64528-65535" jump-target=100.64.1.248/29 src-address=100.64.1.248/29
/ip firewall nat add chain=100.64.1.248/29 comment="100.64.1.248/29 ==> portas=64528-65535" protocol=tcp action=netmap to-addresses=131.100.240.192/29 to-ports=64528-65535
/ip firewall nat add chain=100.64.1.248/29 comment="100.64.1.248/29 ==> portas=64528-65535" protocol=udp action=netmap to-addresses=131.100.240.192/29 to-ports=64528-65535
/ip firewall nat add chain=srcnat comment="100.64.0.0/23 ==> 131.100.240.192/29" action=netmap src-address=100.64.0.0/23 to-addresses=131.100.240.192/29
#Fim do Script