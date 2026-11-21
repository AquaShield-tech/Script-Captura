import platform  # captura dados do sistema operacional (Windows ou Linux)
import os  # serve como ponte para interagir com o sistema operacional e variáveis de ambiente
import socket  # obtém informações de identificação do computador na rede (hostname)
import sys  # permite identificar argumentos de execução (ex: --auto na EC2)
import time  # trabalha com tempo e pausas na execução do programa
import uuid  # gera e trabalha com identificadores únicos (MAC address)
from datetime import datetime  # data e hora

import mysql.connector  # conexão MySQL
import psutil  # obtém informações sobre os recursos e o desempenho do sistema operacional


# Conexão com o banco (usa variáveis de ambiente na EC2 ou os valores padrão locais)
def conectarBanco():
    return mysql.connector.connect(
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "@K8m1m4v3a12"),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", "aquashield")
    )


banco = conectarBanco()
cursor = banco.cursor()

INTERVALO_CAPTURA = 5

LIMITE_CPU_ATENCAO = 50.0
LIMITE_CPU_CRITICO = 80.0

LIMITE_MEMORIA_ATENCAO = 60.0
LIMITE_MEMORIA_CRITICO = 80.0

LIMITE_ARMAZENAMENTO_ATENCAO = 80.0
LIMITE_ARMAZENAMENTO_CRITICO = 90.0

LIMITE_LATENCIA_DISCO_ATENCAO = 25.0
LIMITE_LATENCIA_DISCO_CRITICO = 50.0


def garantirConexao():
    global banco, cursor
    try:
        banco.ping(reconnect=True, attempts=3, delay=2)
    except mysql.connector.Error:
        banco = conectarBanco()
        cursor = banco.cursor()


# lista de mac address da máquina
def obterListaMacs():
    macsAtivos = []
    macsSecundarios = []
    stats = psutil.net_if_stats()

    for nomeInterface, enderecos in psutil.net_if_addrs().items():
        if nomeInterface.lower().startswith("lo"):
            continue

        estaAtiva = stats[nomeInterface].isup if nomeInterface in stats else False

        for endereco in enderecos:
            if endereco.family == psutil.AF_LINK:
                mac = endereco.address.replace("-", ":").strip().upper()
                if len(mac) == 17 and mac not in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
                    # Evita priorizar adaptador virtual do VirtualBox (0A:00:27)
                    if estaAtiva and not mac.startswith("0A:00:27"):
                        if mac not in macsAtivos:
                            macsAtivos.append(mac)
                    else:
                        if mac not in macsSecundarios:
                            macsSecundarios.append(mac)

    listaFinal = macsAtivos + macsSecundarios
    if not listaFinal:
        macNumero = uuid.getnode()
        macFallback = ":".join(["{:02X}".format((macNumero >> (i * 8)) & 0xff) for i in range(5, -1, -1)])
        listaFinal.append(macFallback)

    return listaFinal


listaMacs = obterListaMacs()
macFormatado = listaMacs[0]



def obterIdMedidaPorcentagem():
    cursor.execute(
        "SELECT id FROM medida WHERE unidade_medida = %s LIMIT 1",
        ("%",)
    )

    resultado = cursor.fetchone()

    if resultado is not None:
        return resultado[0]

    cursor.execute(
        "INSERT INTO medida (unidade_medida) VALUES (%s)",
        ("%",)
    )

    banco.commit()

    return cursor.lastrowid

def obterIdAlerta():
    cursor.execute("SELECT id FROM definicao_alerta LIMIT 1")

    resultado = cursor.fetchone()

    if resultado is not None:
        return resultado[0]

    cursor.execute(
        "INSERT INTO definicao_alerta (valor_limite, fk_alerta_descricao) VALUES (%s, %s)",
        (80.0, 2)
    )

    banco.commit()

    return cursor.lastrowid

def verificarMaquina():
    global macFormatado
    listaAtual = obterListaMacs()

    for macItem in listaAtual:
        macLimpo = macItem.replace(":", "")
        cursor.execute(
            """
            SELECT id, mac_address FROM maquina
            WHERE REPLACE(UPPER(mac_address), ':', '') = %s
              AND ativo = 1
            LIMIT 1
            """,
            (macLimpo,)
        )
        resultado = cursor.fetchone()
        if resultado is not None:
            macFormatado = resultado[1]
            return resultado[0]

    macPrincipal = listaAtual[0]
    macFormatado = macPrincipal

    cursor.execute(
        """
        SELECT e.id FROM eta e
        INNER JOIN cco c ON e.fk_cco = c.id
        WHERE c.ativo = 1
        ORDER BY e.id ASC
        LIMIT 1
        """
    )
    resultadoETA = cursor.fetchone()

    if resultadoETA is None:
        print("Nenhuma ETA cadastrada. Cadastre uma ETA pelo site antes de capturar dados.")
        return None

    cursor.execute(
        """
        INSERT INTO maquina
        (hostname, mac_address, sistema_operacional, versao_so, data_cadastro, fk_eta, ativo)
        VALUES (%s, %s, %s, %s, %s, %s, 1)
        """,
        (
            socket.gethostname()[:100],
            macPrincipal,
            platform.system()[:45],
            platform.release()[:45],
            datetime.now(),
            resultadoETA[0]
        )
    )

    banco.commit()
    return cursor.lastrowid

def obterIdComponente(nomeComponente):
    idMedida = obterIdMedidaPorcentagem()

    cursor.execute(
        """
        SELECT id FROM tipo_componente
        WHERE tipo = %s AND fk_medida = %s
        LIMIT 1
        """,
        (nomeComponente, idMedida)
    )

    resultadoTipo = cursor.fetchone()

    if resultadoTipo is None:
        cursor.execute(
            """
            INSERT INTO tipo_componente (tipo, fk_medida)
            VALUES (%s, %s)
            """,
            (nomeComponente, idMedida)
        )

        banco.commit()

        idTipo = cursor.lastrowid

    else:
        idTipo = resultadoTipo[0]

    cursor.execute(
        """
        SELECT id FROM componente
        WHERE fk_tipoComponente = %s
        LIMIT 1
        """,
        (idTipo,)
    )

    resultadoComponente = cursor.fetchone()

    if resultadoComponente is not None:
        return resultadoComponente[0]

    idAlerta = obterIdAlerta()

    cursor.execute(
        """
        INSERT INTO componente
        (valor_maximo, fk_tipoComponente, fk_definicaoAlerta)
        VALUES (%s, %s, %s)
        """,
        (100.0, idTipo, idAlerta)
    )

    banco.commit()

    return cursor.lastrowid


# Garante que a máquina tenha uma configuração ativa para o componente
def garantirConfiguracao(idMaquina, idComponente):
    cursor.execute(
        """
        SELECT fk_statusMaquina FROM configuracao
        WHERE fk_maquina = %s AND fk_componente = %s
        LIMIT 1
        """,
        (idMaquina, idComponente)
    )

    if cursor.fetchone() is not None:
        return

    cursor.execute(
        "SELECT id FROM status_maquina WHERE estado = %s LIMIT 1",
        ("Ativo",)
    )

    resultadoStatus = cursor.fetchone()

    if resultadoStatus is None:
        cursor.execute(
            "INSERT INTO status_maquina (estado) VALUES (%s)",
            ("Ativo",)
        )

        banco.commit()

        idStatus = cursor.lastrowid

    else:
        idStatus = resultadoStatus[0]

    cursor.execute(
        """
        INSERT INTO configuracao
        (fk_maquina, fk_componente, fk_statusMaquina)
        VALUES (%s, %s, %s)
        """,
        (idMaquina, idComponente, idStatus)
    )

    banco.commit()

# faz select no banco para pegar o gatilho do site 
def obterLimitesAtuais(idComponente, nomeComponente):
    limiteAtencao = LIMITE_CPU_ATENCAO
    limiteCritico = LIMITE_CPU_CRITICO
    idDefAtencao = 1
    idDefCritico = 5

    if nomeComponente == "RAM":
        limiteAtencao = LIMITE_MEMORIA_ATENCAO
        limiteCritico = LIMITE_MEMORIA_CRITICO
        idDefAtencao = 2
        idDefCritico = 5
    elif nomeComponente == "Disco":
        limiteAtencao = LIMITE_ARMAZENAMENTO_ATENCAO
        limiteCritico = LIMITE_ARMAZENAMENTO_CRITICO
        idDefAtencao = 3
        idDefCritico = 6

    cursor.execute(
        """
        SELECT
            da.id,
            da.valor_limite,
            da_crit.id,
            da_crit.valor_limite
        FROM componente c
        INNER JOIN definicao_alerta da ON c.fk_definicaoAlerta = da.id
        LEFT JOIN definicao_alerta da_crit ON c.fk_definicaoAlertaCritico = da_crit.id
        WHERE c.id = %s
        LIMIT 1
        """,
        (idComponente,)
    )
    res = cursor.fetchone()
    if res is not None:
        idDefAtencao = res[0]
        limiteAtencao = float(res[1])
        if res[2] is not None and res[3] is not None:
            idDefCritico = res[2]
            limiteCritico = float(res[3])
        else:
            cursor.execute(
                "SELECT valor_limite FROM definicao_alerta WHERE id = %s LIMIT 1",
                (idDefCritico,)
            )
            resCritico = cursor.fetchone()
            if resCritico is not None:
                limiteCritico = float(resCritico[0])

    return idDefAtencao, limiteAtencao, idDefCritico, limiteCritico

# Salva uma leitura no banco de dados e registra alerta quando ultrapassar os limites
def salvarLeitura(idMaquina, idComponente, nomeComponente, valor):
    idMedida = obterIdMedidaPorcentagem()
    valorArredondado = round(float(valor), 2)

    garantirConfiguracao(idMaquina, idComponente)

    cursor.execute(
        """
        INSERT INTO leitura
        (valor, data_hora, fk_medida,
        configuracao_fk_maquina, configuracao_fk_componente)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            valorArredondado,
            datetime.now(),
            idMedida,
            idMaquina,
            idComponente
        )
    )

    idLeitura = cursor.lastrowid
    idDefAtencao, limiteAtencao, idDefCritico, limiteCritico = obterLimitesAtuais(idComponente, nomeComponente)
    fkDefinicaoAlerta = None

    if nomeComponente in ("CPU", "RAM"):
        if valorArredondado > limiteCritico:
            fkDefinicaoAlerta = idDefCritico
        elif valorArredondado >= limiteAtencao:
            fkDefinicaoAlerta = idDefAtencao
    elif nomeComponente == "Disco":
        if valorArredondado >= limiteCritico:
            fkDefinicaoAlerta = idDefCritico
        elif valorArredondado >= limiteAtencao:
            fkDefinicaoAlerta = idDefAtencao

    if fkDefinicaoAlerta is not None:
        cursor.execute(
            "INSERT INTO leitura_alerta (fk_leitura, fk_definicaoAlerta) VALUES (%s, %s)",
            (idLeitura, fkDefinicaoAlerta)
        )

# atividade e a latência do disco
def capturarDadosDisco():
    try:
        io_antes = psutil.disk_io_counters()
        time.sleep(1)
        io_depois = psutil.disk_io_counters()

        if not io_antes or not io_depois:
            return None, None, None

        reads = io_depois.read_count - io_antes.read_count
        writes = io_depois.write_count - io_antes.write_count
        read_time = io_depois.read_time - io_antes.read_time
        write_time = io_depois.write_time - io_antes.write_time

        latenciaLeitura = (read_time / reads) if reads > 0 else 0.0
        latenciaEscrita = (write_time / writes) if writes > 0 else 0.0

        if hasattr(io_depois, "busy_time"):
            busy_time = io_depois.busy_time - io_antes.busy_time
            atividade = min((busy_time / 1000.0) * 100.0, 100.0)
        else:
            atividade = 0.0

        return atividade, latenciaLeitura, latenciaEscrita

    except Exception as erro:
        print("Erro ao consultar os dados do disco:", erro)
        return None, None, None


# Verifica os alertas dos componentes
def gerarAlertas(
    usoCPU,
    usoMemoria,
    armazenamentoDisco,
    espacoLivreGB,
    latenciaLeitura,
    latenciaEscrita
):
    alertasEncontrados = False

    print("\n========== ALERTAS AQUASHIELD ==========")

    # Alertas da CPU
    if usoCPU is not None:
        if usoCPU > LIMITE_CPU_CRITICO:
            print("\nALERTA CRITICO: utilizacao da CPU acima de 80%.")
            print("Uso atual:", round(usoCPU, 2), "%")
            alertasEncontrados = True

        elif usoCPU >= LIMITE_CPU_ATENCAO:
            print("\nATENCAO: utilizacao da CPU acima de 50%.")
            print("Uso atual:", round(usoCPU, 2), "%")
            alertasEncontrados = True

    # Alertas da memória RAM
    if usoMemoria is not None:
        if usoMemoria > LIMITE_MEMORIA_CRITICO:
            print("\nALERTA CRITICO: utilizacao da RAM acima de 80%.")
            print("Uso atual:", round(usoMemoria, 2), "%")
            alertasEncontrados = True

        elif usoMemoria >= LIMITE_MEMORIA_ATENCAO:
            print("\nATENCAO: utilizacao da RAM acima de 60%.")
            print("Uso atual:", round(usoMemoria, 2), "%")
            alertasEncontrados = True

    # Alertas de armazenamento
    if armazenamentoDisco is not None:
        if armazenamentoDisco >= LIMITE_ARMAZENAMENTO_CRITICO:
            print("\nALERTA CRITICO: disco com 90% ou mais de ocupacao.")
            print("Ocupacao atual:", round(armazenamentoDisco, 2), "%")
            alertasEncontrados = True

        elif armazenamentoDisco >= LIMITE_ARMAZENAMENTO_ATENCAO:
            print("\nATENCAO: disco com 80% ou mais de ocupacao.")
            print("Ocupacao atual:", round(armazenamentoDisco, 2), "%")
            alertasEncontrados = True

        if espacoLivreGB is not None:
            print("Espaco livre:", round(espacoLivreGB, 2), "GB")

    # Alertas de latência de leitura
    if latenciaLeitura is not None:
        if latenciaLeitura > LIMITE_LATENCIA_DISCO_CRITICO:
            print("\nALERTA CRITICO: latencia de leitura acima de 50 ms.")
            print("Latencia atual:", round(latenciaLeitura, 2), "ms")
            alertasEncontrados = True

        elif latenciaLeitura > LIMITE_LATENCIA_DISCO_ATENCAO:
            print("\nATENCAO: latencia de leitura acima de 25 ms.")
            print("Latencia atual:", round(latenciaLeitura, 2), "ms")
            alertasEncontrados = True

    # Alertas de latência de escrita
    if latenciaEscrita is not None:
        if latenciaEscrita > LIMITE_LATENCIA_DISCO_CRITICO:
            print("\nALERTA CRITICO: latencia de escrita acima de 50 ms.")
            print("Latencia atual:", round(latenciaEscrita, 2), "ms")
            alertasEncontrados = True

        elif latenciaEscrita > LIMITE_LATENCIA_DISCO_ATENCAO:
            print("\nATENCAO: latencia de escrita acima de 25 ms.")
            print("Latencia atual:", round(latenciaEscrita, 2), "ms")
            alertasEncontrados = True

    if not alertasEncontrados:
        print("Nenhum alerta foi identificado nesta captura.")


# dados dos componentes da máquina
def capturarDados():
    garantirConexao()
    idMaquina = verificarMaquina()

    if idMaquina is None:
        return

    print("\n================================")
    print("       CAPTURA AQUASHIELD")
    print("================================")

    print("Maquina:", socket.gethostname())
    print("MAC:", macFormatado)

    usoCPU = psutil.cpu_percent(interval=1)
    usoMemoria = psutil.virtual_memory().percent

    # ocupação e o espaço livre do disco do sistema
    try:
        disco = psutil.disk_usage(os.path.abspath(os.sep))
        armazenamentoDisco = disco.percent
        espacoLivreGB = disco.free / (1024 ** 3)

    except OSError as erro:
        print("Nao foi possivel consultar o espaco do disco:", erro)
        armazenamentoDisco = None
        espacoLivreGB = None

    # atividade e a latência do disco
    (
        atividadeDisco,
        latenciaLeitura,
        latenciaEscrita
    ) = capturarDadosDisco()

    # valores capturados
    print("\nUso da CPU:", round(usoCPU, 2), "%")
    print("Uso da memoria RAM:", round(usoMemoria, 2), "%")

    if armazenamentoDisco is not None:
        print(
            "Armazenamento ocupado (Uso do Disco:",
            str(round(armazenamentoDisco, 2)) + "%)"
        )

    if atividadeDisco is not None:
        print("Atividade do disco:", round(atividadeDisco, 2), "%")

    if latenciaLeitura is not None:
        print("Latencia de leitura:", round(latenciaLeitura, 2), "ms")

    if latenciaEscrita is not None:
        print("Latencia de escrita:", round(latenciaEscrita, 2), "ms")

    # Componentes salvo no banco (alinhado com a tabelas de tipo_componente, IDs 1,2,3,4)
    capturas = [
        ("CPU", usoCPU),
        ("RAM", usoMemoria),
        ("Disco", armazenamentoDisco)
    ]

    try:
        for nomeComponente, percentual in capturas:
            if percentual is None:
                print(nomeComponente + ": nao capturado.")
                continue

            idComponente = obterIdComponente(nomeComponente)

            salvarLeitura(
                idMaquina,
                idComponente,
                nomeComponente,
                percentual
            )

        if espacoLivreGB is not None:
            print(
                "Espaco livre no disco:",
                round(espacoLivreGB, 2),
                "GB"
            )

        banco.commit()

        print("\nLimites de alertas:")
        print("Uso da CPU: atencao em 50%; critico acima de 80%.")
        print("Uso da memória RAM: atencao em 60%; critico acima de 80%.")
        print("Armazenamento: atencao em 80%; critico em 90%.")
        print("Latencia do disco: atencao acima de 25 ms; critico acima de 50 ms.")

        gerarAlertas(
            usoCPU,
            usoMemoria,
            armazenamentoDisco,
            espacoLivreGB,
            latenciaLeitura,
            latenciaEscrita
        )

        print("\n[OK] Capturas salvas no banco de dados aquashield.")

    except mysql.connector.Error as erro:
        banco.rollback()
        print("Erro ao salvar a captura:", erro)

def visualizarDados():
    garantirConexao()
    idMaquina = verificarMaquina()

    if idMaquina is None:
        return

    cursor.execute(
        """
        SELECT tc.tipo, l.data_hora, l.valor, m.unidade_medida
        FROM leitura l
        INNER JOIN configuracao cfg
            ON l.configuracao_fk_maquina = cfg.fk_maquina
            AND l.configuracao_fk_componente = cfg.fk_componente
        INNER JOIN componente c
            ON cfg.fk_componente = c.id
        INNER JOIN tipo_componente tc
            ON c.fk_tipoComponente = tc.id
        INNER JOIN medida m
            ON l.fk_medida = m.id
        WHERE cfg.fk_maquina = %s
        ORDER BY l.data_hora DESC
        LIMIT 50
        """,
        (idMaquina,)
    )

    registros = cursor.fetchall()

    print("\nDados Capturados")

    if not registros:
        print("Nenhum dado capturado para esta maquina.")
        return

    print("Componente | Data/Hora | Valor")
    print("--------------------------------------")

    for linha in registros:
        print(
            linha[0],
            "|",
            linha[1],
            "|",
            str(linha[2]) + " " + linha[3]
        )

def capturaAutomatica():
    print("\n===== CAPTURA AUTOMATICA =====")
    print("Nova captura a cada", INTERVALO_CAPTURA, "segundos.")
    print("Pressione Ctrl+C para parar.")

    try:
        while True:
            capturarDados()
            time.sleep(INTERVALO_CAPTURA)

    except KeyboardInterrupt:
        print("\nCaptura automatica encerrada.")

# verificação para ec2 e manual na máquina local
# modo automático via argumento (--auto) ou menu interativo no terminal
try:
    if "--auto" in sys.argv or os.getenv("MODO_AUTOMATICO", "").lower() == "true":
        capturaAutomatica()
    else:
        while True:
            print("\n==============================")
            print("          AQUASHIELD")
            print("==============================")
            print("MAC:", macFormatado)
            print("\n1 - Capturar dados")
            print("2 - Captura automatica")
            print("3 - Ver dados")
            print("4 - Sair")

            opcao = input("Escolha uma opcao: ")

            if opcao == "1":
                capturarDados()

            elif opcao == "2":
                capturaAutomatica()

            elif opcao == "3":
                visualizarDados()

            elif opcao == "4":
                print("Encerrando programa...")
                break

            else:
                print("Opcao invalida.")

except KeyboardInterrupt:
    print("\nPrograma interrompido.")

finally:
    try:
        cursor.close()
        banco.close()

    except mysql.connector.Error:
        pass