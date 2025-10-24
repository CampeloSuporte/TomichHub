
#====== Script CGNAT by Redes Brasil ==========================
        
/ip firewall address-list add list=rede-privada-para-cgnat address=100.63.0.0/20
/ip firewall nat add action=jump chain=srcnat comment="Jump ==> CGNAT" jump-target=cgnat src-address-list=rede-privada-para-cgnat
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.0.0/25 portas=1024-3039" jump-target=100.63.0.0/25 src-address=100.63.0.0/25
/ip firewall nat add chain=100.63.0.0/25 comment="100.63.0.0/25 ==> portas=1024-3039" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=1024-3039
/ip firewall nat add chain=100.63.0.0/25 comment="100.63.0.0/25 ==> portas=1024-3039" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=1024-3039
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.0.128/25 portas=3040-5055" jump-target=100.63.0.128/25 src-address=100.63.0.128/25
/ip firewall nat add chain=100.63.0.128/25 comment="100.63.0.128/25 ==> portas=3040-5055" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=3040-5055
/ip firewall nat add chain=100.63.0.128/25 comment="100.63.0.128/25 ==> portas=3040-5055" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=3040-5055
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.1.0/25 portas=5056-7071" jump-target=100.63.1.0/25 src-address=100.63.1.0/25
/ip firewall nat add chain=100.63.1.0/25 comment="100.63.1.0/25 ==> portas=5056-7071" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=5056-7071
/ip firewall nat add chain=100.63.1.0/25 comment="100.63.1.0/25 ==> portas=5056-7071" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=5056-7071
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.1.128/25 portas=7072-9087" jump-target=100.63.1.128/25 src-address=100.63.1.128/25
/ip firewall nat add chain=100.63.1.128/25 comment="100.63.1.128/25 ==> portas=7072-9087" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=7072-9087
/ip firewall nat add chain=100.63.1.128/25 comment="100.63.1.128/25 ==> portas=7072-9087" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=7072-9087
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.2.0/25 portas=9088-11103" jump-target=100.63.2.0/25 src-address=100.63.2.0/25
/ip firewall nat add chain=100.63.2.0/25 comment="100.63.2.0/25 ==> portas=9088-11103" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=9088-11103
/ip firewall nat add chain=100.63.2.0/25 comment="100.63.2.0/25 ==> portas=9088-11103" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=9088-11103
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.2.128/25 portas=11104-13119" jump-target=100.63.2.128/25 src-address=100.63.2.128/25
/ip firewall nat add chain=100.63.2.128/25 comment="100.63.2.128/25 ==> portas=11104-13119" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=11104-13119
/ip firewall nat add chain=100.63.2.128/25 comment="100.63.2.128/25 ==> portas=11104-13119" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=11104-13119
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.3.0/25 portas=13120-15135" jump-target=100.63.3.0/25 src-address=100.63.3.0/25
/ip firewall nat add chain=100.63.3.0/25 comment="100.63.3.0/25 ==> portas=13120-15135" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=13120-15135
/ip firewall nat add chain=100.63.3.0/25 comment="100.63.3.0/25 ==> portas=13120-15135" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=13120-15135
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.3.128/25 portas=15136-17151" jump-target=100.63.3.128/25 src-address=100.63.3.128/25
/ip firewall nat add chain=100.63.3.128/25 comment="100.63.3.128/25 ==> portas=15136-17151" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=15136-17151
/ip firewall nat add chain=100.63.3.128/25 comment="100.63.3.128/25 ==> portas=15136-17151" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=15136-17151
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.4.0/25 portas=17152-19167" jump-target=100.63.4.0/25 src-address=100.63.4.0/25
/ip firewall nat add chain=100.63.4.0/25 comment="100.63.4.0/25 ==> portas=17152-19167" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=17152-19167
/ip firewall nat add chain=100.63.4.0/25 comment="100.63.4.0/25 ==> portas=17152-19167" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=17152-19167
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.4.128/25 portas=19168-21183" jump-target=100.63.4.128/25 src-address=100.63.4.128/25
/ip firewall nat add chain=100.63.4.128/25 comment="100.63.4.128/25 ==> portas=19168-21183" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=19168-21183
/ip firewall nat add chain=100.63.4.128/25 comment="100.63.4.128/25 ==> portas=19168-21183" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=19168-21183
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.5.0/25 portas=21184-23199" jump-target=100.63.5.0/25 src-address=100.63.5.0/25
/ip firewall nat add chain=100.63.5.0/25 comment="100.63.5.0/25 ==> portas=21184-23199" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=21184-23199
/ip firewall nat add chain=100.63.5.0/25 comment="100.63.5.0/25 ==> portas=21184-23199" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=21184-23199
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.5.128/25 portas=23200-25215" jump-target=100.63.5.128/25 src-address=100.63.5.128/25
/ip firewall nat add chain=100.63.5.128/25 comment="100.63.5.128/25 ==> portas=23200-25215" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=23200-25215
/ip firewall nat add chain=100.63.5.128/25 comment="100.63.5.128/25 ==> portas=23200-25215" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=23200-25215
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.6.0/25 portas=25216-27231" jump-target=100.63.6.0/25 src-address=100.63.6.0/25
/ip firewall nat add chain=100.63.6.0/25 comment="100.63.6.0/25 ==> portas=25216-27231" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=25216-27231
/ip firewall nat add chain=100.63.6.0/25 comment="100.63.6.0/25 ==> portas=25216-27231" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=25216-27231
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.6.128/25 portas=27232-29247" jump-target=100.63.6.128/25 src-address=100.63.6.128/25
/ip firewall nat add chain=100.63.6.128/25 comment="100.63.6.128/25 ==> portas=27232-29247" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=27232-29247
/ip firewall nat add chain=100.63.6.128/25 comment="100.63.6.128/25 ==> portas=27232-29247" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=27232-29247
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.7.0/25 portas=29248-31263" jump-target=100.63.7.0/25 src-address=100.63.7.0/25
/ip firewall nat add chain=100.63.7.0/25 comment="100.63.7.0/25 ==> portas=29248-31263" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=29248-31263
/ip firewall nat add chain=100.63.7.0/25 comment="100.63.7.0/25 ==> portas=29248-31263" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=29248-31263
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.7.128/25 portas=31264-33279" jump-target=100.63.7.128/25 src-address=100.63.7.128/25
/ip firewall nat add chain=100.63.7.128/25 comment="100.63.7.128/25 ==> portas=31264-33279" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=31264-33279
/ip firewall nat add chain=100.63.7.128/25 comment="100.63.7.128/25 ==> portas=31264-33279" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=31264-33279
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.8.0/25 portas=33280-35295" jump-target=100.63.8.0/25 src-address=100.63.8.0/25
/ip firewall nat add chain=100.63.8.0/25 comment="100.63.8.0/25 ==> portas=33280-35295" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=33280-35295
/ip firewall nat add chain=100.63.8.0/25 comment="100.63.8.0/25 ==> portas=33280-35295" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=33280-35295
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.8.128/25 portas=35296-37311" jump-target=100.63.8.128/25 src-address=100.63.8.128/25
/ip firewall nat add chain=100.63.8.128/25 comment="100.63.8.128/25 ==> portas=35296-37311" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=35296-37311
/ip firewall nat add chain=100.63.8.128/25 comment="100.63.8.128/25 ==> portas=35296-37311" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=35296-37311
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.9.0/25 portas=37312-39327" jump-target=100.63.9.0/25 src-address=100.63.9.0/25
/ip firewall nat add chain=100.63.9.0/25 comment="100.63.9.0/25 ==> portas=37312-39327" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=37312-39327
/ip firewall nat add chain=100.63.9.0/25 comment="100.63.9.0/25 ==> portas=37312-39327" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=37312-39327
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.9.128/25 portas=39328-41343" jump-target=100.63.9.128/25 src-address=100.63.9.128/25
/ip firewall nat add chain=100.63.9.128/25 comment="100.63.9.128/25 ==> portas=39328-41343" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=39328-41343
/ip firewall nat add chain=100.63.9.128/25 comment="100.63.9.128/25 ==> portas=39328-41343" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=39328-41343
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.10.0/25 portas=41344-43359" jump-target=100.63.10.0/25 src-address=100.63.10.0/25
/ip firewall nat add chain=100.63.10.0/25 comment="100.63.10.0/25 ==> portas=41344-43359" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=41344-43359
/ip firewall nat add chain=100.63.10.0/25 comment="100.63.10.0/25 ==> portas=41344-43359" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=41344-43359
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.10.128/25 portas=43360-45375" jump-target=100.63.10.128/25 src-address=100.63.10.128/25
/ip firewall nat add chain=100.63.10.128/25 comment="100.63.10.128/25 ==> portas=43360-45375" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=43360-45375
/ip firewall nat add chain=100.63.10.128/25 comment="100.63.10.128/25 ==> portas=43360-45375" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=43360-45375
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.11.0/25 portas=45376-47391" jump-target=100.63.11.0/25 src-address=100.63.11.0/25
/ip firewall nat add chain=100.63.11.0/25 comment="100.63.11.0/25 ==> portas=45376-47391" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=45376-47391
/ip firewall nat add chain=100.63.11.0/25 comment="100.63.11.0/25 ==> portas=45376-47391" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=45376-47391
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.11.128/25 portas=47392-49407" jump-target=100.63.11.128/25 src-address=100.63.11.128/25
/ip firewall nat add chain=100.63.11.128/25 comment="100.63.11.128/25 ==> portas=47392-49407" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=47392-49407
/ip firewall nat add chain=100.63.11.128/25 comment="100.63.11.128/25 ==> portas=47392-49407" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=47392-49407
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.12.0/25 portas=49408-51423" jump-target=100.63.12.0/25 src-address=100.63.12.0/25
/ip firewall nat add chain=100.63.12.0/25 comment="100.63.12.0/25 ==> portas=49408-51423" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=49408-51423
/ip firewall nat add chain=100.63.12.0/25 comment="100.63.12.0/25 ==> portas=49408-51423" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=49408-51423
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.12.128/25 portas=51424-53439" jump-target=100.63.12.128/25 src-address=100.63.12.128/25
/ip firewall nat add chain=100.63.12.128/25 comment="100.63.12.128/25 ==> portas=51424-53439" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=51424-53439
/ip firewall nat add chain=100.63.12.128/25 comment="100.63.12.128/25 ==> portas=51424-53439" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=51424-53439
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.13.0/25 portas=53440-55455" jump-target=100.63.13.0/25 src-address=100.63.13.0/25
/ip firewall nat add chain=100.63.13.0/25 comment="100.63.13.0/25 ==> portas=53440-55455" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=53440-55455
/ip firewall nat add chain=100.63.13.0/25 comment="100.63.13.0/25 ==> portas=53440-55455" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=53440-55455
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.13.128/25 portas=55456-57471" jump-target=100.63.13.128/25 src-address=100.63.13.128/25
/ip firewall nat add chain=100.63.13.128/25 comment="100.63.13.128/25 ==> portas=55456-57471" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=55456-57471
/ip firewall nat add chain=100.63.13.128/25 comment="100.63.13.128/25 ==> portas=55456-57471" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=55456-57471
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.14.0/25 portas=57472-59487" jump-target=100.63.14.0/25 src-address=100.63.14.0/25
/ip firewall nat add chain=100.63.14.0/25 comment="100.63.14.0/25 ==> portas=57472-59487" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=57472-59487
/ip firewall nat add chain=100.63.14.0/25 comment="100.63.14.0/25 ==> portas=57472-59487" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=57472-59487
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.14.128/25 portas=59488-61503" jump-target=100.63.14.128/25 src-address=100.63.14.128/25
/ip firewall nat add chain=100.63.14.128/25 comment="100.63.14.128/25 ==> portas=59488-61503" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=59488-61503
/ip firewall nat add chain=100.63.14.128/25 comment="100.63.14.128/25 ==> portas=59488-61503" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=59488-61503
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.15.0/25 portas=61504-63519" jump-target=100.63.15.0/25 src-address=100.63.15.0/25
/ip firewall nat add chain=100.63.15.0/25 comment="100.63.15.0/25 ==> portas=61504-63519" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=61504-63519
/ip firewall nat add chain=100.63.15.0/25 comment="100.63.15.0/25 ==> portas=61504-63519" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=61504-63519
/ip firewall nat add action=jump chain=cgnat comment="Jump para rede ==> 100.63.15.128/25 portas=63520-65535" jump-target=100.63.15.128/25 src-address=100.63.15.128/25
/ip firewall nat add chain=100.63.15.128/25 comment="100.63.15.128/25 ==> portas=63520-65535" protocol=tcp action=netmap to-addresses=45.4.179.128/25 to-ports=63520-65535
/ip firewall nat add chain=100.63.15.128/25 comment="100.63.15.128/25 ==> portas=63520-65535" protocol=udp action=netmap to-addresses=45.4.179.128/25 to-ports=63520-65535
/ip firewall nat add chain=srcnat comment="100.63.0.0/20 ==> 45.4.179.128/25" action=netmap src-address=100.63.0.0/20 to-addresses=45.4.179.128/25
#Fim do Script