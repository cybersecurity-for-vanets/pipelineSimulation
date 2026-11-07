# Calibração da Etapa 1 com os dados da CET (MSVP 2016–2019)

A calibração usa só os relatórios *Mobilidade no Sistema Viário Principal — Volumes e Velocidades* (MSVP) da CET-SP: 2019 (`docs/relatorio-msvp-2019-revisao-2-junho-22.pdf`) e 2016–2018 (`docs/msvp_<ano>/`). A TomTom deixou de ser usada porque não há dados dela desses anos. O código TomTom foi removido no branch `cet-calibration`.

## Dados

O MSVP traz, por via e sentido, em dias úteis:

- **Volume:** contagem classificada manual (automóveis, ônibus urbanos e fretados, caminhões, motos e bicicletas) das 7h às 10h e das 17h às 20h, por faixa horária (seções 5.1.3 e 5.1.4).
- **Velocidade:** velocidade média no espaço de um carro-teste com GPS que percorre o trecho no fluxo (distância do trecho ÷ tempo de percurso), nas mesmas faixas horárias, por trecho e sentido (seções 5.3.2 e 5.3.3).

As linhas usadas estão em `conf/cet_volumes.csv` e `conf/cet_speeds.csv`, com o ano em `ano` e a rota da simulação em `route`.

### Cobertura por ano

| Ano | highway (23 de Maio) | urban_road (Consolação) | intersection (Nove de Julho / São Gabriel) |
|---|---|---|---|
| 2019 | volume e velocidade | volume e velocidade | volume (Nove de Julho) e velocidade (as duas) |
| 2018 | só volume (o MSVP 2018 não tem pesquisa de velocidade) | só volume | não pesquisado |
| 2017 | não pesquisado | volume e velocidade | só velocidade da Nove de Julho (sem nível: falta o volume) |
| 2016 | volume e velocidade | não pesquisado | não pesquisado |

Os relatórios de 2016 e 2017 têm outro formato:

- **Volume.** Não há tabela por faixa horária, só a tabela da rota com janelas de 1 h deslocadas de 15 em 15 min. Usaram-se as janelas de hora cheia e o total simples. A data é o intervalo da pesquisa, porque o relatório não dá o dia.
- **Velocidade.** Vem por viagem do carro-teste, e as viagens 1/2/3 saem por volta das 7h/8h/9h e 17h/18h/19h (método, p. 18 do MSVP 2017). A viagem N foi tratada como a hora N. A velocidade do trecho é a distância total ÷ o tempo somado dos sub-trechos. A hora de saída é a do início da rota inteira: o carro chega ao trecho 15–33 min depois na urban_road 2017 (Centro) e 25–30 min depois na highway 2016 (Centro). Isso está anotado na coluna `observacao`.
- **Comprimento.** O trecho de 2016 da 23 de Maio tem 2.550 m (2.498 m em 2019); o da Consolação em 2017, 1.900 m (1.922 m).

## Trechos

| Topologia | Trecho de velocidade (CET) | Ponto de contagem | Demanda |
|---|---|---|---|
| highway | Av. 23 de Maio, Vd. Brig. Luís Antônio ↔ Vd. Paraíso (2.498 m), mai/2019 | Viad. Beneficência Portuguesa (26 e 30/04/2019) | observada |
| urban_road | R. da Consolação, Av. Paulista ↔ R. Rêgo Freitas (1.922 m), mar/2019 | R. Caio Prado – R. Marquês de Paranaguá (27/03/2019) | observada |
| intersection, via principal | Av. Nove de Julho, Av. Cidade Jardim ↔ Av. São Gabriel (1.351 m), jun/2019 | R. Manuel Guedes – R. Emanuel Kant (23 e 27/05/2019) | observada |
| intersection, transversal | Av. São Gabriel, Av. Nove de Julho ↔ Av. Antônio J. de M. Andrade (907/902 m), mai/2019 | não há | inferida |

Por que estes trechos:

- **Highway.** A 23 de Maio é via expressa sem semáforo, tem velocidade e volume na mesma pista e nos dois sentidos. As Marginais não servem: a CET mediu a velocidade nas pistas locais, não na expressa.
- **Intersection.** Nove de Julho × São Gabriel é o único cruzamento em nível entre duas vias pesquisadas que se encontrou. Pça. Padre Aleixo Mafra (Av. São Miguel → Av. Mal. Tito), Pça. Enzo Ferrari (Av. Interlagos → Av. Teotônio Vilela), Pça. Min. Pedro Chaves (Av. Washington Luís → Av. Interlagos) e Av. São Miguel → Av. Gov. Carvalho Pinto são a mesma via mudando de nome, confirmado na geometria do OSM.

### Mapeamento na rede SUMO

`fetch_data.py` liga os pontos de cada trecho (`conf/routes.json`) por arestas SUMO. Depois tira das pontas as arestas mais curtas que 20 m e as alças (`highway.*_link`). As extremidades dos trechos ficam em junções, e o `--junctions.join` deixa ali tocos de 0,2–0,8 m. Num toco desses um carro de 5 m não consegue ser inserido. No primeiro teste, isso travou a inserção da Consolação: só 462 de 1.160 veículos entraram em 900 s. O ponto do Vd. Paraíso também caía numa alça de 1 faixa ao lado da pista principal da 23 de Maio. No início da rota também sai a aresta de 1 faixa quando a rota tem 3 ou mais faixas em outro ponto: a intersection_main_centro começava em 113 m da Av. Cidade Jardim (1 faixa por sentido) e num semáforo de 1 faixa, onde os ~1.600 veic/h da Nove de Julho faziam fila antes do trecho.

Comprimento no SUMO contra o trecho CET, depois do corte:

| Rota | SUMO (m) | CET (m) |
|---|---|---|
| highway_centro | 2.157 | 2.498 |
| highway_bairro | 2.488 | 2.498 |
| urban_road_centro / bairro | 1.803 / 1.823 | 1.922 |
| intersection_main_centro / bairro | 1.319 / 1.274 | 1.351 |
| intersection_cross_centro / bairro | 819 / 884 | 907 / 902 |

O highway_centro perde os primeiros ~340 m, que no OSM são a alça do Vd. Paraíso. A comparação usa a velocidade, não o tempo, então a diferença de comprimento entra só pela diferença de trecho coberto.

O caminho mais curto entre os pontos pode sair de uma avenida de pistas paralelas por um conector curto e voltar pela pista vizinha. Na Nove de Julho isso acontecia na R. Turquia e na R. Alemanha: as rotas principais tinham 2–3 conversões artificiais. O veículo cedia passagem dentro da junção, e a Nove de Julho sentido bairro escoava ~30 veic/h. `sumo_route_utils.straighten_route` troca cada conversão pelo movimento direto quando ele reencontra a rota em até 12 arestas, sem ser mais de 1,3× + 30 m mais longo. As rotas principais da intersection ficaram sem conversões.

### Reparos da rede (`src/network_repair.py`)

Em 2026-11-05, com o perfil calibrado, a validação mostrava fila de inserção de 400–2.200 s e velocidade simulada muito abaixo da CET (urban_road centro a 3–4 km/h contra 8–31 km/h). A causa era a rede, não o Krauss:

- **Faixas que seguem em frente.** O OSM não tem `turn:lanes` na Consolação, e o netconvert distribui as faixas entre as saídas de cada junção agrupada. Em dois cruzamentos do sentido centro, só 1 das 4 faixas seguia em frente (as outras viravam à esquerda ou iam para a faixa de ônibus no contrafluxo). Reparo: onde menos faixas seguem em frente do que min(faixas de entrada, faixas de saída), as faixas da direita passam a seguir em frente também (faixa compartilhada). Foram 7 junções na urban_road e 5 na intersection. Só conta como "seguindo em frente" a faixa que chega ao fim da rota sem ser desviada para uma saída mais adiante (passada de trás para a frente).
- **Verde da via principal.** Sem plano semafórico no OSM, o netconvert cria um ciclo de 90 s que divide o verde entre todas as aproximações. Em junções com muitas aproximações, a Consolação ficava com 15–28% de verde. Reparo: cada sentido com contagem CET tem pelo menos `network.min_main_green_share` (55%) do ciclo, e as demais fases verdes têm pelo menos `network.min_other_green_s` (10 s). Ciclo e amarelos não mudam. Um programa linear escolhe o plano mais próximo do original. Onde não há plano que atenda ao mínimo, maximiza o menor verde (34% no cruzamento de muitas fases do fim da Consolação). O valor de 55% é hipótese do projeto, porque os planos da CET não são públicos. No teste de sensibilidade com `krauss.default` na urban_road, 45% deixou fila no sentido bairro, e 55% e 65% deram velocidades a até 2 km/h uma da outra.

Os dois reparos rodam em `fetch_data.py` depois do mapeamento e ficam embutidos no `.net.xml`. Rodar de novo não muda nada (idempotente).

- **Junções vizinhas unidas (`network.join_nodes`).** Na Nove de Julho sentido bairro, os semáforos da R. Alemanha e da R. Turquia ficaram em dois nós a 33 m um do outro. Entre eles, o SUMO só via uma faixa que continuava sem convergência obrigatória adiante, e todo o volume passava por ela: ~1.000 veic/h contra 1.475–1.629 da CET, com fila de inserção de ~1.500 s e ~3 km/h em todas as horas. Os dois nós OSM (1389167160 e 1389167162) passam a ser uma junção só no netconvert (`<join>` num arquivo de nós). Com `krauss.default`, a Nove de Julho sentido bairro passa a escoar todo o volume, sem fila de inserção. A rede da highway e a da urban_road não mudam: as faixas, as conexões e as fases saem iguais.

### Alças da highway

A CET conta o volume num ponto (Viad. Beneficência Portuguesa, `count_point` em `conf/routes.json`). Ao longo dos 2,5 km do trecho, veículos entram e saem por alças. Simular o volume contado como viagens de ponta a ponta punha 7.500–8.300 veic/h na redução de 5 para 3 faixas do fim da highway_centro (onde 2 faixas são de saída) e nas 3 faixas do início da highway_bairro. A fila voltava pela via inteira.

`sumo_route_utils.route_ramps` acha as alças sem semáforo entre as pontas da rota. Ficam de fora as entradas que só têm ligação secundária (dê a preferência) para uma faixa da rota: com mais de 1.000 veic/h na faixa, o veículo da alça espera uma brecha que o SUMO não dá, e nenhum entra (933595463 na bairro e 172433906#1 na centro deixavam ~1.900 veículos na fila de inserção). Na via real a entrada é em zíper; o volume delas fica com a origem da rota. Cada alça leva a fração das faixas que usa na junção (faixa compartilhada com a rota conta meia), supondo fluxo igual por faixa. As entradas ficam limitadas a `network.max_ramp_veh_h_per_lane` (1.800 veic/h por faixa da alça, perto do fluxo de saturação de uma faixa), e o excedente fica com a origem da via principal. `generate_routes.route_demands` acha o fluxo da origem que dá o volume CET no ponto de contagem e cria um fluxo por par entrada–saída. Só a viagem inteira (`flow_<rota>`) entra na velocidade da calibração. A fração das faixas não diz quanto sai de fato, e cada saída antes do ponto de contagem aumenta o fluxo que tem de entrar pela origem: com as frações cheias, as arestas a montante da contagem na highway_bairro recebiam 2.000–2.400 veic/h por faixa, e a fila de inserção (que entra no tempo de viagem) definia a velocidade. Como a CET mediu tráfego fluindo no ponto de contagem, as saídas antes dele são reduzidas na mesma proporção até nenhuma aresta passar do fluxo por faixa da contagem (ou zeradas, se não bastar). Os veículos das alças partem pelo menos 100 m antes da junção, porque o SUMO não consegue inserir a fração de uma alça numa aresta de 35 m.

| Rota | Entradas (fração) | Saídas (fração) |
|---|---|---|
| highway_centro | nenhuma (172433906#1 é dê a preferência) | 172433907#0 (0,29), 38738766 (0,10), 16975795 (0,40) |
| highway_bairro | 454557951 (0,25), 937280724#1 (0,20), 256314554#2 (0,17) | 172433904#0 e 16652807 (zeradas pelo limite da contagem), 799528120 (0,10) |

Com 8.262 veic/h às 7h de 2019, a highway_bairro fica com 1.150–1.720 veic/h por faixa ao longo da rota (1.652 na contagem) e ~1.240 veic/h por alça de entrada, sem fila de inserção na viagem inteira. Testou-se a junção do tipo `zipper` para as entradas em dê a preferência, e ela piorou a fila.

## Método

O método segue Khaleghian et al. (SMARTCOMP 2023, resumo em `docs/resumo_calibrating_real_world_city_traffic_simulation.md`): velocidade observada como alvo, calibração num período e validação em outro. Todos os parâmetros estão em `conf/calibration_params.json`.

1. **Demanda observada.** O fluxo SUMO de cada rota com `demand: cet_volume` é o volume CET da hora em equivalente-carro (`cet.pce`: automóvel 1,0; ônibus e caminhão 1,5; moto 0,4; bicicleta 0). O SUMO simula só automóveis, então o equivalente-carro representa a ocupação da via pelos demais veículos. Os fatores são hipótese do projeto, não valores da CET (o relatório publica só o volume simples). O fluxo não é inferido da velocidade, que é o cuidado do §10.3 do resumo.
2. **Demanda inferida (Av. São Gabriel).** Sem contagem CET, o fluxo de cada nível é achado por bisseção, em escala log, entre `flow.min_veh_h` e `flow.max_veh_h_per_lane` × faixas do gargalo. O alvo é a velocidade CET da hora, com a Nove de Julho no fluxo observado. A bisseção roda uma vez com `krauss.default` e é refeita com o perfil Krauss escolhido. Como essa velocidade foi usada para achar o fluxo, o erro da São Gabriel não valida o modelo; o relatório o mostra à parte (`inferred_demand_fit`).
3. **Semáforos.** Em `topologies.signal_coordinated` (urban_road e intersection), os offsets vêm do `tlsCoordinator.py` do SUMO (onda verde), ponderados pelo fluxo médio da tarde. As rotas de demanda inferida entram com metade do menor fluxo observado, para que a via com contagem tenha prioridade.
4. **Krauss.** Um perfil por topologia, comum a todas as rotas e horas dela, com os parâmetros de `krauss.bounds` (`accel`, `decel`, `tau`, `sigma`, `minGap`, `speedFactor`, `speedDev`). O TPE (Optuna) minimiza o RMSE da velocidade (km/h) nos níveis de calibração (`cet.calibration`: todas as horas de 2019; na intersection, 7h, 9h, 17h e 19h, em `hours_by_topology`), só nas rotas de demanda observada. O primeiro trial é `krauss.default`. A busca para em `krauss.n_trials`, quando o RMSE relativo chega a `krauss.target_relative_rmse` ou quando o melhor trial não muda em `krauss.stagnation_patience` trials guiados.
5. **Validação.** Todo nível observado fora da calibração que tenha velocidade CET: os outros anos com velocidade (highway 2016, urban_road 2017) e, na intersection, que só tem volume de 2019, as horas retidas (8h e 18h). A primeira rodada calibrava só em 2019 17–19h. Nessas horas a lentidão vem em boa parte da fila que chega de fora da rede (ver Limitações), e o TPE escolhia perfis de baixa capacidade (`speedFactor` 0,74–0,79, `tau` até 1,9 s) que travavam todas as manhãs da validação. Calibrar com manhã e tarde força um perfil que sirva para os dois regimes. O relatório final simula todos os níveis com `simulation.report_seeds` e grava, por rota e nível, o fluxo, a velocidade CET, a simulada, o desvio entre seeds e o erro. Também grava o RMSE, o MAE e o viés de calibração, de validação e de validação por ano.
6. **Critério de validação.** A topologia é aprovada quando o RMSE relativo da validação (RMSE / velocidade CET média dos níveis de validação, só rotas de demanda observada) fica em até `acceptance.max_validation_relative_rmse` (25%, ordem de grandeza do erro de validação de Khaleghian et al. 2023). O resultado por rota é informativo. O veredito fica em `report.summary.verdict` de `conf/krauss_calibration.json`. Referência: a calibração de 2026-10-31, anterior aos reparos da rede, reprovava nas três topologias (41% highway, 51% intersection, 85% urban_road).

Cada nível é uma simulação SUMO de uma hora, com `simulation.seconds` e demanda constante. Entram as viagens com partida pretendida depois de `simulation.warmup_s`, e o tempo de viagem inclui o `departDelay` (ver `calibration/simulation.py`).

## Níveis de fluxo do experimento V2V

Cada nível vira um cenário do experimento V2V (`conf/flow_levels.json`, contrato em `src/flow_levels.py`):

- **Observados (`y<ano>h<hora>`).** Cada ano e hora em que todas as rotas de demanda observada da topologia têm volume CET: 18 na highway (2016, 2018, 2019), 18 na urban_road (2017, 2018, 2019) e 6 na intersection (2019). Na São Gabriel, os níveis sem velocidade CET usam o fluxo inferido na mesma hora de 2019.
- **Sintéticos (`s<fator×100>`).** `cet.synthetic.factors` (0,25×, 0,50×, 0,75× e 1,25×) × o fluxo médio de cada rota nos níveis de `cet.synthetic.reference_year` (2019). Não têm observação. Servem para cobrir volumes que a CET não pesquisou: todas as horas da CET são de pico, então os níveis observados ficam numa faixa estreita. Nos gráficos, os pontos sintéticos aparecem com marcação própria.

São 22 níveis na highway, 22 na urban_road e 10 na intersection: 54 cenários × 4 protocolos × 10 seeds = 2.160 simulações OMNeT++.

## Limitações

- **Só horários de pico.** A CET não cobre madrugada nem entrepico. Os níveis sintéticos preenchem essa faixa, mas sem observação.
- **Anos diferentes.** A rede e os semáforos são os de hoje para todos os anos.
- **Amostras pequenas.** A contagem é de um dia, e a velocidade vem de poucas passagens do carro-teste por hora. Volume e velocidade de um mesmo trecho foram medidos em dias, e às vezes meses, diferentes.
- **Divergência no relatório.** Na Nove de Julho (R. Manuel Guedes – R. Emanuel Kant), a tabela de hora pico (p. 65) traz 14/05/2019 com 4.635 veic/h no sentido Centro, e a tabela por faixa horária traz 27/05/2019 com no máximo 1.990 veic/h. Usou-se a tabela por faixa horária, a única com as 6 horas.
- **Equivalente-carro.** Os fatores de `cet.pce` são hipótese. As motos são 12–39% do volume nesses pontos.
- **Demanda inferida.** O fluxo da São Gabriel não é observado. Nas horas em que a velocidade pouco responde ao fluxo, a bisseção pode ficar sem alvo (`stop_reason` em `conf/route_flows_calibrated.json`).
- **Congestionamento que vem de fora do recorte.** Com volume parecido, a CET mede velocidades muito diferentes: Consolação sentido centro, 26 km/h às 7h e 8 km/h às 18h; Nove de Julho sentido centro, 21 e 8 km/h. A lentidão da tarde vem da fila que volta do centro da cidade, que não está na rede simulada. Nenhum perfil Krauss reproduz as duas horas com o mesmo volume. O perfil calibrado com manhã e tarde fica entre os dois regimes, com erro de sinais opostos nessas horas, e a densidade da tarde fica subestimada nos cenários V2V.
- **Planos semafóricos e alças.** A fração mínima de verde (`network.min_main_green_share`) e a divisão do volume pelas alças (proporcional às faixas, com teto por alça e limitada pelo fluxo por faixa da contagem) são hipóteses, não dados da CET. As entradas em dê a preferência da 23 de Maio ficam sem fluxo próprio.
- **urban_road e intersection não validadas.** Reprovam no critério de 25% (57,9% e 72,4%) e seguem para o experimento V2V com o perfil calibrado, como limitação. Foi considerado ampliar o recorte até o gargalo de jusante (centro, na Consolação; Av. Cidade Jardim/Faria Lima, na Nove de Julho), para que a fila que volta até o trecho se formasse no modelo. Isso exigiria volumes das transversais e conversões que o MSVP 2019 não traz, planos semafóricos desses cruzamentos e uma demanda externa ajustada por hora com as mesmas velocidades usadas na validação. A melhora no RMSE viria desses parâmetros extras, não de dados observados, e a opção não foi seguida (2026-11-06).
- **Validação da intersection.** Só há volume de 2019, então a validação usa horas retidas do mesmo dia de pesquisa (8h e 18h), não um período independente.
- **Faixa de ônibus.** A Consolação tem corredor de ônibus à esquerda (`lanes:bus=1` no OSM), mas o netconvert libera as 4 faixas para carros.

## Resultados

Rodada completa de 2026-11-06 (calibração em 2019 inteiro; intersection em 7, 9, 17 e 19h). A intersection foi recalibrada sozinha (`--topology intersection`) depois da união das junções da R. Alemanha e da R. Turquia; antes dela, o resultado era 81,3% (perfil 1,90; 3,81; 1,33; 0,66; 3,41; 0,54; 0,18). Detalhe por rota e nível em `conf/krauss_calibration.json`.

| Topologia | Perfil Krauss (accel, decel, tau, sigma, minGap, speedFactor, speedDev) | RMSE calibração | RMSE validação | Relativo | Veredito |
|---|---|---|---|---|---|
| highway | 2,49; 3,69; 0,57; 0,20; 1,80; 0,72; 0,19 | 8,6 km/h (n=12) | 8,2 km/h (n=12, 2016) | 22,6% | aprovada |
| urban_road | 3,48; 2,66; 0,56; 0,18; 3,29; 0,93; 0,27 | 9,0 km/h (n=12) | 10,6 km/h (n=12, 2017) | 57,9% | reprovada |
| intersection | 3,13; 3,96; 1,91; 0,09; 2,79; 0,57; 0,20 | 5,7 km/h (n=8) | 6,0 km/h (n=4, 8h e 18h) | 72,4% | reprovada |

- **highway.** Aprovada, mas a velocidade simulada quase não varia com o nível (35–39 km/h para CET de 22 a 49 km/h): o perfil acerta a média, não a variação entre horas. Sem fila de inserção relevante (só 2018 7–8h, nível sem velocidade CET).
- **urban_road.** Fila de inserção de 100–800 s em 13 dos 18 níveis, sobretudo nas manhãs de 2017 (2.300–2.900 veic/h por sentido): a rede reparada ainda não escoa o volume CET. O sentido centro fica rápido demais à tarde (fila externa) e lento demais de manhã.
- **intersection.** Com a união das junções, a rede escoa o volume CET com `krauss.default` (Nove de Julho sentido bairro a ~31 km/h, sem fila). A CET, porém, mede 2,3 km/h às 17h e 18h nesse sentido (36 min para 1,35 km) e 11–15 km/h nas outras horas, com volumes parecidos (1.411–1.629 veic/h). Para chegar perto das horas lentas, o TPE escolheu de novo um perfil de baixa capacidade (`tau` 1,91 s, `speedFactor` 0,57), que volta a criar fila de inserção (630–990 s) e deixa o sentido bairro a 4–6 km/h em todas as horas. O RMSE absoluto de validação é baixo (6,0 km/h), mas a velocidade CET média da validação é ~8 km/h, então o relativo fica em 72%.

Nas duas topologias reprovadas, a CET mede velocidades muito diferentes com o mesmo volume, e a diferença vem de congestionamento de fora do recorte simulado (ver Limitações). Um perfil Krauss único não reproduz os dois regimes: o TPE fica entre eles e reduz a capacidade da rede, o que cria fila de inserção. Decisão de 2026-11-06: urban_road e intersection seguem para o experimento V2V com o perfil calibrado, marcados como não validados pelo critério de 25%.
