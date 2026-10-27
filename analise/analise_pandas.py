"""Análises do Sentinel Access a partir do CSV exportado pelo painel."""
from pathlib import Path
import pandas as pd

CSV_PATH = Path(__file__).resolve().parents[1] / "logs_acesso.csv"


def carregar_logs(caminho=CSV_PATH):
    df = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
    df["ocorrido_em"] = pd.to_datetime(df["ocorrido_em"], errors="coerce", utc=True).dt.tz_convert(None)
    df["dia"] = df["ocorrido_em"].dt.date
    return df.sort_values("ocorrido_em")


def permanencia_por_colaborador(df):
    linhas = []
    for (matricula, nome, dia), grupo in df[df["tipo"].isin(["ENTRADA", "SAIDA"])].groupby(["matricula", "nome_snapshot", "dia"], dropna=False):
        entrada = None
        total = pd.Timedelta(0)
        for _, evento in grupo.sort_values("ocorrido_em").iterrows():
            if evento["tipo"] == "ENTRADA":
                entrada = evento["ocorrido_em"]
            elif evento["tipo"] == "SAIDA" and entrada is not None:
                if evento["ocorrido_em"] >= entrada:
                    total += evento["ocorrido_em"] - entrada
                entrada = None
        linhas.append({"dia": dia, "matricula": matricula, "colaborador": nome,
                       "permanencia_minutos": round(total.total_seconds() / 60, 2),
                       "intervalo_aberto": entrada is not None})
    return pd.DataFrame(linhas)


def executar(caminho=CSV_PATH):
    df = carregar_logs(caminho)
    print("Entradas por dia")
    print(df[df.tipo.eq("ENTRADA")].groupby("dia").size().rename("entradas"))
    print("\nSaídas por dia")
    print(df[df.tipo.eq("SAIDA")].groupby("dia").size().rename("saidas"))
    print("\nAcessos negados por dia")
    print(df[df.tipo.eq("ACESSO_NEGADO")].groupby("dia").size().rename("negados"))
    print("\nTentativas com tags desconhecidas por dia")
    print(df[df.tipo.eq("TAG_DESCONHECIDA")].groupby("dia").size().rename("tags_desconhecidas"))
    print("\nColaboradores com mais tentativas negadas")
    print(df[df.tipo.eq("ACESSO_NEGADO")].groupby("nome_snapshot").size().sort_values(ascending=False).head(10))
    print("\nPermanência por colaborador e dia")
    print(permanencia_por_colaborador(df))


if __name__ == "__main__":
    executar()
