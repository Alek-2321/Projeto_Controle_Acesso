# Sentinel Access — Sistema de Controle de Acesso RFID

## Sobre o projeto

O Sentinel Access é um projeto acadêmico desenvolvido com o objetivo de demonstrar um sistema de controle de acesso a uma sala restrita utilizando cartões RFID.

A proposta é identificar colaboradores, verificar se possuem autorização para acessar o ambiente e registrar entradas, saídas e tentativas de acesso não autorizadas. O sistema também foi pensado para continuar realizando verificações com base nos dados armazenados localmente na Raspberry Pi quando o servidor estiver temporariamente indisponível.

## Funcionalidades

- Leitura de cartões RFID por meio de um leitor conectado à Raspberry Pi.
- Cadastro e gerenciamento de colaboradores.
- Verificação das permissões de acesso.
- Registro de entradas, saídas e tentativas negadas ou desconhecidas.
- Painel web para consultar colaboradores e acompanhar os eventos.
- Armazenamento de dados em banco SQLite.
- Cache local na Raspberry Pi para apoiar a operação durante uma indisponibilidade do servidor.
- Sincronização dos eventos armazenados localmente quando a conexão é restabelecida.
- Análise dos registros com Pandas para obter informações sobre a utilização da sala e as tentativas de acesso.

## Objetivo acadêmico

O projeto busca aplicar conhecimentos de desenvolvimento web, integração entre hardware e software, banco de dados e análise de dados. A solução reúne a leitura física dos cartões, a validação das permissões e o registro das ocorrências em um sistema integrado.

## Tecnologias utilizadas

- Python
- FastAPI
- SQLite
- Raspberry Pi
- RFID (MFRC522)
- HTML, CSS e JavaScript
- Pandas e Jupyter Notebook

## Estrutura geral

- `main.py`: aplicação de servidor e API do sistema.
- `static/`: arquivos da interface web.
- `rfid_reader.py`: leitura RFID na Raspberry Pi e suporte ao cache local.
- `analise/`: arquivos utilizados para análise dos dados com Pandas.