# Convenções para scripts de gráficos

Este documento define as regras que os scripts de gráficos desta pasta seguem. Novos tipos de gráfico (boxplot, barras com IC, heatmap, linhas por fluxo etc.) devem respeitá-las, para que as imagens fiquem consistentes entre si. O script de referência é `plot_sratter.py` (dispersão por semente).

## 1. Entrada de dados

- Os scripts de `ploting/` só **leem CSVs já calculados** em `results/rawData/`. Eles não calculam métricas.
- Não leia `scavetool_export.csv` aqui. O arquivo passa de 2 GB e a agregação pertence a `src/flow_kpis_by_topology.py`.
- Fontes permitidas:

| Arquivo | Conteúdo | Uso típico |
| --- | --- | --- |
| `flow_kpis_per_seed.csv` | uma linha por execução (semente) | dispersão, boxplot, violino |
| `flow_kpis_by_topology.csv` | média e IC 95% por topologia, fluxo e protocolo | barras com IC, linhas, heatmap |
| `flow_kpis_by_distance.csv` | PDR médio e IC 95% por topologia, fluxo, protocolo e faixa de distância (50 m), com `Flow per lane (veh/h/lane)` | curvas PDR × distância |

- Colunas disponíveis nos dois arquivos: `Topology`, `Scenario`, `Protocol`, `PDR (%)`, `CBR (%)`, `Latency E2E (ms)`. O arquivo por semente também tem `run` e `Seed`, e o resumo tem `N` e as colunas `… CI95`.
- `Scenario` é o nível de fluxo, de dois tipos:
  - **observado**: volume CET de um ano e uma hora pesquisados, id `y<ano>h<hora>` (`y2019h07` = 2019, 07:00–08:00);
  - **sintético**: fator × fluxo médio observado, sem observação, id `s<fator×100>` (`s050` = 0,50×). Serve para cobrir volumes que a CET não mediu.
- **Cada topologia tem a sua lista de níveis.** Desde 2026-11-06 são um por LOS do HCM (4–6 por topologia, todos `s<fator×100>`, `kind` sintético, com `los` em `conf/flow_levels.json` e `level_los()`); `s100` é o pico CET. Prefira o LOS ou o veic/h/faixa no eixo, não o fator. A lista vem de `conf/flow_levels.json`, lido só por `src/flow_levels.py` (`topology_level_ids()`, `flow_level_ids()` para a união, `level_label()`, `level_sort_key()`, `is_synthetic()`, `flow_per_lane()`). Não escreva a lista de níveis à mão e não ordene por hora: use `level_sort_key()` (observados por ano/hora, depois sintéticos por fator) ou o próprio volume.
- Os arquivos têm `Flow per lane (veh/h/lane)` (`veh_h_per_lane` de `conf/flow_levels.json`, gravado por `generate_routes.py`), `Level kind` (`observed`/`synthetic`) e `Survey year` (vazio para sintético).
- O arquivo por semente também tem `Active vehicles`: média de veículos simultâneos na janela de medição (soma de `measurementTimeS` / `MEASUREMENT_S`).
- O PDR só é consistente somado por execução: as recepções esperadas são contadas no emissor e as recebidas no receptor. Não calcule PDR por nó.
- Se um gráfico precisar de uma métrica que não está nesses CSVs, **inclua a métrica em `src/flow_kpis_by_topology.py`** e regere o CSV. Não a recalcule dentro do script de plot.
- Não altere nem sobrescreva os CSVs de entrada a partir de um script de gráfico.

## 2. Estrutura do script

- Nome do arquivo: `plot_<tipo>.py`, em minúsculas e com underscore.
- Organização em seções numeradas, como em `plot_sratter.py`:
  1. `DIRETÓRIOS E CONFIGURAÇÕES`: caminhos e constantes.
  2. `DESENHO`: funções que recebem um `axis` e os dados, e só desenham.
  3. `EXECUÇÃO`: funções `plot_*` que montam a figura, e `main()`.
- Caminhos partem de `PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`. Não use caminhos absolutos nem dependa do diretório de execução.
- A ordem de protocolos vem de `load_protocols()`, importado de `src/protocols.py` com `sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))`. Não escreva a lista de protocolos à mão.
- Dependências: apenas `matplotlib`, `pandas` e `numpy`, que já são usadas no projeto. Não adicione bibliotecas novas sem necessidade.
- A função de desenho (`draw_*`) não cria figuras, não salva arquivos e não adiciona legendas. Assim, o mesmo código serve para o modo único e para o modo separado.

## 3. Argumentos de linha de comando

- Todo script que desenha mais de uma métrica deve aceitar `--modo {unico,separado}`, com padrão `unico`:
  - `unico`: todas as métricas numa imagem, uma ao lado da outra (`1 × N` painéis).
  - `separado`: uma imagem por métrica.
- Outros argumentos só são aceitos se mudarem o que é desenhado (por exemplo, filtrar uma topologia). Caminhos de entrada e saída são constantes do script, não argumentos.
- Use `argparse`, com `description` e `help` em português.

## 4. Padrão visual

### 4.1 Cores e marcadores

| Elemento | Codificação | Valores |
| --- | --- | --- |
| Topologia | **cor** | `highway` `#1f77b4`, `urbanRoad` `#ff7f0e`, `intersection` `#2ca02c` |
| Protocolo | **marcador** | `o`, `s`, `^`, `D`, `v`, `P`, na ordem de `load_protocols()` |
| Média do grupo | traço preto `_` | `s=400`, `linewidths=2`, `zorder=3` |
| Origem da demanda | **preenchimento** do marcador e opacidade da linha | demanda CET (pico `s100` ou nível observado `y<ano>h<hora>`): marcador cheio, linha opaca; demanda extrapolada (demais níveis por LOS): marcador vazado (branco por dentro) e segmento esmaecido (`EXTRAPOLATED_LINE_ALPHA`) quando uma das pontas é extrapolada |
| LOS do HCM | letra no topo do painel (`annotate_los()`) | na posição x de cada nível; em negrito no pico CET |
| Nível (quando não está no eixo X, ex. PDR × distância) | **estilo da linha** | até quatro níveis por topologia, pelo papel: LOS mais leve (`:`), LOS intermediário (`-.`), pico CET (`--`) e acima do pico (`-`); o título do painel diz o LOS e o volume de cada um |

- Em gráficos sobre o fluxo, o eixo X é **numérico**, `Flow per lane (veh/h/lane)`, e não uma categoria por nível; o LOS de cada nível vai no topo do painel com `annotate_los()`. Quando topologias diferentes dividem o eixo, use escala log (o highway tem centenas a milhares de veh/h/faixa, as vias urbanas dezenas).
- Linhas que ligam níveis seguem a ordem do volume (x), não a do id.
- Demanda CET × extrapolada é obrigatório em todo gráfico que mostre níveis: use `plot_level_series()` (linhas com IC) e `scatter_levels()` (dispersão) de `src/level_style.py`, reexportados por `plot_common.py`, e inclua na legenda `level_origin_handles()` ("demanda CET (pico)" e "demanda extrapolada"). Só a demanda CET está na faixa em que o modelo de tráfego foi calibrado e validado.
- Não há cor nem tom por nível (o antigo `level_shade()` por hora foi retirado). Não crie dicionários de estilo por nível: a lista muda quando `map_los.py` é refeito.
- Heatmaps têm uma linha por (topologia, LOS), em ordem de volume (ver `src/generate_kpi_heatmaps.py`); o rótulo diz o LOS, o veh/h/faixa e se a demanda é o pico CET ou extrapolada.

- Mantenha a mesma cor para a mesma topologia e o mesmo marcador para o mesmo protocolo em todos os gráficos. Se um tipo de gráfico não suportar marcadores (barras, por exemplo), codifique o protocolo com a mesma ordem de posição e deixe a topologia como cor.
- Essas constantes, as traduções e `save()` ficam em `plot_common.py`. Importe de lá em vez de copiar os dicionários.

### 4.2 Textos

- Todo texto visível fica em **português** e **não pode reproduzir o texto bruto do CSV**. Isso vale para os valores de `Scenario` (por exemplo, `y2019h07`, `s050`) e para os nomes de colunas (por exemplo, `Latency E2E (ms)`).
- Traduções já definidas em `plot_sratter.py`:

| Tipo | Chave no CSV | Texto exibido |
| --- | --- | --- |
| Nível de fluxo observado (rótulo) | `y2019h07`, `y2016h18`, … | `07h–08h/2019`, `18h–19h/2016`, … (`level_label()`) |
| Nível por LOS (rótulo) | `s045`, `s100`, `s120`, … | `0,45×`, `1,00× (pico CET)`, `1,20×`, … (`level_label()`); LOS com `los_of()` |
| Origem da demanda (legenda) | pico CET ou observado / demais níveis | demanda CET (pico) / demanda extrapolada |
| Título do painel | `PDR (%)` / `CBR (%)` / `Latency E2E (ms)` | PDR / CBR / Latência E2E |
| Título do eixo Y | `PDR (%)` | Taxa de entrega de pacotes (%) |
| Título do eixo Y | `CBR (%)` | Taxa de ocupação do canal (%) |
| Título do eixo Y | `Latency E2E (ms)` | Latência fim a fim (ms) |
| Título do eixo X | `Flow per lane (veh/h/lane)` | Volume por faixa (veh/h/faixa); acrescente `, escala log` quando o eixo for log |

- Reutilize esses textos. Para uma métrica ou categoria nova, adicione a tradução ao dicionário correspondente antes de usá-la.
- Todo eixo tem título, e o eixo da métrica inclui a unidade. O título do painel é curto e sem unidade.
- Não há título geral (`suptitle`) por padrão.

### 4.3 Legendas

- As legendas ficam **abaixo dos painéis**, em `figure.legend`, e nunca dentro da área de plotagem, para não cobrir pontos. Use `figure.tight_layout(rect=(0, 0.2, 1, 1))` para reservar o espaço.
- Elas são, de baixo para cima: a marca estatística (`Marca preta = média de cada grupo`), a origem do nível (observado (CET) / sintético) e, acima delas, lado a lado, Topologia (cor) e Protocolo (marcador).
- Se um novo gráfico usar outra marca estatística (mediana, IC, caixa), inclua uma entrada na legenda que explique o elemento, como é feito para a média.
- Legendas incluem só categorias presentes nos dados. Não liste protocolos ausentes.

## 5. Saída

- Pasta: `results/plots/flow_kpis/`. Crie-a com `os.makedirs(..., exist_ok=True)`.
- Nomes de arquivo:
  - modo único: `<tipo>_metricas.png`
  - modo separado: `<tipo>_<metrica>.png`, com `pdr`, `cbr` e `latencia_e2e`
  - Exemplo: `scatter_metricas.png`, `scatter_pdr.png`.
- Formato PNG, `dpi=200`. Feche cada figura com `plt.close(figure)` depois de salvar.
- Tamanhos de figura: `5 × N` por `5` polegadas no modo único e `7 × 5` no modo separado, para N métricas.
- Ao salvar, imprima `[OK] Gráfico salvo em: '<caminho>'`, como as demais etapas do pipeline.

## 6. Robustez

- O CSV atual só tem `baseline`, e os protocolos FF1 virão depois. O script deve funcionar com qualquer subconjunto de protocolos e de topologias, sem escrever `baseline` no código.
- Células sem dados são ignoradas, sem gerar erro nem painel vazio.
- Quando o CSV de entrada não existir, falhe com uma mensagem que indique qual script o gera (`src/flow_kpis_by_topology.py`).

## 7. Verificação antes de entregar

1. Rodar os dois modos (`--modo unico` e `--modo separado`).
2. Abrir as imagens geradas e conferir que nenhuma legenda cobre pontos e que não há texto cortado.
3. Conferir que nenhum rótulo visível é texto bruto do CSV.
4. Conferir uma ou duas médias contra `flow_kpis_by_topology.csv`.
