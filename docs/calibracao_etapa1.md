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

`fetch_data.py` liga os pontos de cada trecho (`conf/routes.json`) por arestas SUMO. Depois tira das pontas as arestas mais curtas que 20 m e as alças (`highway.*_link`). As extremidades dos trechos ficam em junções, e o `--junctions.join` deixa ali tocos de 0,2–0,8 m. Num toco desses um carro de 5 m não consegue ser inserido. No primeiro teste, isso travou a inserção da Consolação: só 462 de 1.160 veículos entraram em 900 s. O ponto do Vd. Paraíso também caía numa alça de 1 faixa ao lado da pista principal da 23 de Maio.

Comprimento no SUMO contra o trecho CET, depois do corte:

| Rota | SUMO (m) | CET (m) |
|---|---|---|
| highway_centro | 2.157 | 2.498 |
| highway_bairro | 2.488 | 2.498 |
| urban_road_centro / bairro | 1.803 / 1.823 | 1.922 |
| intersection_main_centro / bairro | 1.356 / 1.272 | 1.351 |
| intersection_cross_centro / bairro | 824 / 884 | 907 / 902 |

O highway_centro perde os primeiros ~340 m, que no OSM são a alça do Vd. Paraíso. A comparação usa a velocidade, não o tempo, então a diferença de comprimento entra só pela diferença de trecho coberto.

## Método

O método segue Khaleghian et al. (SMARTCOMP 2023, resumo em `docs/resumo_calibrating_real_world_city_traffic_simulation.md`): velocidade observada como alvo, calibração num período e validação em outro. Todos os parâmetros estão em `conf/calibration_params.json`.

1. **Demanda observada.** O fluxo SUMO de cada rota com `demand: cet_volume` é o volume CET da hora em equivalente-carro (`cet.pce`: automóvel 1,0; ônibus e caminhão 1,5; moto 0,4; bicicleta 0). O SUMO simula só automóveis, então o equivalente-carro representa a ocupação da via pelos demais veículos. Os fatores são hipótese do projeto, não valores da CET (o relatório publica só o volume simples). O fluxo não é inferido da velocidade, que é o cuidado do §10.3 do resumo.
2. **Demanda inferida (Av. São Gabriel).** Sem contagem CET, o fluxo de cada nível é achado por bisseção, em escala log, entre `flow.min_veh_h` e `flow.max_veh_h_per_lane` × faixas do gargalo. O alvo é a velocidade CET da hora, com a Nove de Julho no fluxo observado. A bisseção roda uma vez com `krauss.default` e é refeita com o perfil Krauss escolhido. Como essa velocidade foi usada para achar o fluxo, o erro da São Gabriel não valida o modelo; o relatório o mostra à parte (`inferred_demand_fit`).
3. **Semáforos.** Em `topologies.signal_coordinated` (urban_road e intersection), os offsets vêm do `tlsCoordinator.py` do SUMO (onda verde), ponderados pelo fluxo médio da tarde. As rotas de demanda inferida entram com metade do menor fluxo observado, para que a via com contagem tenha prioridade.
4. **Krauss.** Um perfil por topologia, comum a todas as rotas e horas dela, com os parâmetros de `krauss.bounds` (`accel`, `decel`, `tau`, `sigma`, `minGap`, `speedFactor`, `speedDev`). O TPE (Optuna) minimiza o RMSE da velocidade (km/h) nos níveis de calibração (`cet.calibration`: 2019, 17h, 18h e 19h), só nas rotas de demanda observada. O primeiro trial é `krauss.default`. A busca para em `krauss.n_trials`, quando o RMSE relativo chega a `krauss.target_relative_rmse` ou quando o melhor trial não muda em `krauss.stagnation_patience` trials guiados.
5. **Validação.** Todo nível observado fora da calibração que tenha velocidade CET: as manhãs de 2019 e os outros anos com velocidade (highway 2016, urban_road 2017). O relatório final simula todos os níveis com `simulation.report_seeds` e grava, por rota e nível, o fluxo, a velocidade CET, a simulada, o desvio entre seeds e o erro. Também grava o RMSE, o MAE e o viés de calibração, de validação e de validação por ano.

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

## Resultados

Ainda pendentes. Esta seção é preenchida depois da rodada completa de `calibrate_stage1.py`.
