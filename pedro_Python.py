"""
 

Instalar:  pip install psutil mysql-connector-python
Rodar:     python coletor.py
"""

import os
import platform
import socket
import time
import uuid
from datetime import datetime

import mysql.connector
import psutil

 
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "pedro2302",
    "database": "aquashield",
}

# Nomes iguais aos de Tipo_Componente.tipo.
COMPONENTES = ["Memoria RAM"]   # opções: "CPU", "Memoria RAM", "Armazenamento"

LIMITE_FALHA = 90   # % que conta como FALHA (mantenha igual a @limite_falha no consultas_dash.sql)

INTERVALO = 5     # segundos entre leituras
ID_ETA = None     # None = usa a primeira ETA cadastrada (só importa se a máquina ainda não existir)


 
LEITORES = {
    "CPU": lambda: psutil.cpu_percent(interval=1),
    "Memoria RAM": lambda: psutil.virtual_memory().percent,
    "Armazenamento": lambda: psutil.disk_usage(os.path.abspath(os.sep)).percent,
}


def mac_address():
    """MAC no formato AA:BB:CC:DD:EE:FF (17 caracteres, como a coluna exige)."""
    n = uuid.getnode()
    return ":".join(f"{(n >> i) & 0xFF:02X}" for i in range(40, -1, -8))


 
def preparar(cursor):
    # ETA da máquina  
    if ID_ETA is not None:
        id_eta = ID_ETA
    else:
        cursor.execute("SELECT id FROM ETA ORDER BY id LIMIT 1")
        linha = cursor.fetchone()
        if not linha:
            raise RuntimeError("Nenhuma ETA cadastrada. Rode o script do banco primeiro.")
        id_eta = linha[0]

    # Máquina: identificada pelo MAC; cadastra se ainda não existir
    mac = mac_address()
    cursor.execute("SELECT id FROM Maquina WHERE mac_address = %s", (mac,))
    linha = cursor.fetchone()
    if linha:
        id_maquina = linha[0]
    else:
        cursor.execute(
            "INSERT INTO Maquina (hostname, mac_address, sistema_operacional, versao_so, data_cadastro, fk_eta) "
            "VALUES (%s, %s, %s, %s, NOW(), %s)",
            (socket.gethostname(), mac, platform.system()[:45], platform.release()[:10], id_eta),
        )
        id_maquina = cursor.lastrowid
        print(f"Máquina cadastrada (id {id_maquina}).")

    cursor.execute("SELECT id FROM Status_Maquina WHERE estado = 'Ativo'")
    id_ativo = cursor.fetchone()[0]

    # Cada componente: id, medida, alerta e limite (vêm de Componente / Tipo_Componente / Definicao_Alerta)
    componentes = []
    for tipo in COMPONENTES:
        cursor.execute(
            "SELECT c.id, tc.fk_medida, da.id, da.valor_limite "
            "FROM Componente c "
            "JOIN Tipo_Componente tc ON tc.id = c.fk_tipoComponente "
            "JOIN Definicao_Alerta da ON da.id = c.fk_definicaoAlerta "
            "WHERE tc.tipo = %s",
            (tipo,),
        )
        linha = cursor.fetchone()
        if not linha:
            raise RuntimeError(f"Componente '{tipo}' não encontrado no banco.")
        id_comp, id_medida, id_def, limite = linha

        # Leitura exige uma Configuracao (maquina + componente). INSERT IGNORE = não duplica.
        cursor.execute(
            "INSERT IGNORE INTO Configuracao (fk_maquina, fk_componente, fk_statusMaquina) VALUES (%s, %s, %s)",
            (id_maquina, id_comp, id_ativo),
        )
        componentes.append(
            {"tipo": tipo, "id": id_comp, "medida": id_medida, "definicao": id_def, "limite": float(limite)}
        )
    return id_maquina, componentes


# =====================================================
# GRAVAÇÃO
# =====================================================
def gravar_leituras(cursor, id_maquina, componentes):
    for comp in componentes:
        valor = LEITORES[comp["tipo"]]()
        cursor.execute(
            "INSERT INTO Leitura (valor, data_hora, fk_medida, configuracao_fk_maquina, configuracao_fk_componente) "
            "VALUES (%s, NOW(), %s, %s, %s)",
            (valor, comp["medida"], id_maquina, comp["id"]),
        )
        id_leitura = cursor.lastrowid

        acima = valor > comp["limite"]      # alerta (limite do banco)
        falha = valor > LIMITE_FALHA        # falha (90%)
        if acima:
            cursor.execute(
                "INSERT INTO Leitura_Alerta (fk_leitura, fk_definicaoAlerta) VALUES (%s, %s)",
                (id_leitura, comp["definicao"]),
            )
        marca = ""
        if falha:
            marca = f"  << FALHA (acima de {LIMITE_FALHA}%)"
        elif acima:
            marca = f"  < alerta (acima de {comp['limite']:.0f}%)"
        print(f"{datetime.now():%H:%M:%S}  {comp['tipo']:<14} {valor:5.1f}%{marca}")


# =====================================================
# LOOP PRINCIPAL
# =====================================================
def main():
    conexao = mysql.connector.connect(**DB_CONFIG)
    cursor = conexao.cursor()
    id_maquina, componentes = preparar(cursor)
    conexao.commit()
    conexao.close()

    print(f"Máquina {id_maquina} | monitorando {', '.join(COMPONENTES)} a cada {INTERVALO}s | Ctrl+C para parar\n")

    try:
        while True:
            try:
                conexao = mysql.connector.connect(**DB_CONFIG)   # conexão por ciclo: se o banco cair, volta sozinho
                cursor = conexao.cursor()
                gravar_leituras(cursor, id_maquina, componentes)
                conexao.commit()
                conexao.close()
            except mysql.connector.Error as erro:
                print(f"{datetime.now():%H:%M:%S}  banco indisponível ({erro}). Tentando de novo...")
            time.sleep(INTERVALO)
    except KeyboardInterrupt:
        print("\nMonitoramento encerrado.")


if __name__ == "__main__":
    main()