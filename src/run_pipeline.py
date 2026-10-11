import os
import sys
import subprocess
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")

def run_step(step_name, script_name):
    print(f"\n==================================================")
    print(f" Executando: {step_name} ({script_name})")
    print(f"==================================================")
    script_path = os.path.join(SRC_DIR, script_name)
    
    start_time = time.time()
    result = subprocess.run([sys.executable, script_path], cwd=BASE_DIR)
    elapsed = time.time() - start_time
    
    if result.returncode != 0:
        print(f"[ERRO CRÍTICO] Falha na etapa '{step_name}'. Pipeline interrompido.")
        sys.exit(1)
    
    print(f"[OK] {step_name} concluído em {elapsed:.2f}s.")

def main():
    print("==================================================")
    print(" INICIANDO PIPELINE COMPLETO DE SIMULAÇÃO (VANET)")
    print(" Matriz: 3 Topologias x 3 Densidades x 4 Protocolos")
    print(" Total: 36 Configurações | 1.080 Simulações")
    print("==================================================")

    # Etapa 1: Coleta de Dados TomTom e Geração de Redes SUMO
    # run_step("Etapa 1.1 - Coleta de Mapas OSM e Dados TomTom", "fetch_data.py")
    # run_step("Etapa 1.2 - Geração de Rotas XML", "generate_routes.py")

    # Etapa 2: Regerar omnetpp.ini e Estruturas de Configuração
    run_step("Etapa 2 - Configurações OMNeT++", "generate_stage2_configs.py")

    # # Etapa 3: Execução Paralela no Apple M1 (36 configs x 30 seeds)
    run_step("Etapa 3 - Orquestração de Simulações", "orchestrate_stage3.py")

    # # Etapa 4: Extração com opp_scavetool e Geração de Gráficos
    # run_step("Etapa 4 - Análise Estatística e Plotagem", "analyze_stage4_results.py")

    print("\n==================================================")
    print(" PIPELINE FINALIZADO COM SUCESSO!")
    print(f" Relatórios e Gráficos disponíveis em: {os.path.join(BASE_DIR, 'results')}")
    print("==================================================")

if __name__ == "__main__":
    main()