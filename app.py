import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522
from datetime import datetime
import time, csv

GPIO.setmode(GPIO.BOARD)

#So setando os pinos
GPIO.setup(31,GPIO.OUT) # Led Verde
GPIO.setup(29,GPIO.OUT) # Led Vermelho

GPIO.output(31, GPIO.LOW)
GPIO.output(29, GPIO.LOW)
usuarioAtual = None
horarioEntrada = ""
horarioSaida = ""
tempoPermanencia = ""

usuariosCadastrados = {
    703695879170: {
        "nome": "Henrique",
        "acesso": True
    },
    1045162096093: {
        "nome": "Tomás",
        "acesso": False
    }
}

def registrarLog(id, usuario, evento):
    with open("logs.csv", "a", newline="") as arquivo:
        escritor = csv.writer(arquivo)

        escritor.writerow([
            datetime.now(),
            id,
            usuario,
            evento
        ])

entrou = False

leitorRFID = SimpleMFRC522()

try:
    while True:
            print("Aproxime o cartão do leitor...")
            id, texto = leitorRFID.read()
            if id in usuariosCadastrados and usuariosCadastrados[id]["acesso"] == True:
                if entrou:
                       print("Bye bye " + usuarioAtual)
                       horarioSaida = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                       print(horarioSaida)
                       registrarLog(id, usuarioAtual, "Saída")
                       tempoPermanencia = datetime.strptime(horarioSaida, "%d/%m/%Y %H:%M:%S") - datetime.strptime(horarioEntrada, "%d/%m/%Y %H:%M:%S")
                       print("Tempo de permanência: " + str(tempoPermanencia))
                       entrou = False
                else:          
                    usuarioAtual = usuariosCadastrados[id]["nome"]
                    print(f"Bem-vindo, {usuarioAtual}!")
                    horarioEntrada = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                    print(horarioEntrada)
                    entrou = True
                    registrarLog(id, usuarioAtual, "Entrada")
                GPIO.output(31, GPIO.HIGH)
                time.sleep(2)
                GPIO.output(31, GPIO.LOW)
            else:
                if id in usuariosCadastrados:
                    print("Usuário sem permissão de acesso.")
                    registrarLog(id, usuariosCadastrados[id]["nome"], "Acesso negado")
                else:
                    print("Usuário não cadastrado.")
                    registrarLog(id, "Desconhecido", "Acesso negado")

                GPIO.output(29, GPIO.HIGH)
                time.sleep(2)
                GPIO.output(29, GPIO.LOW)
                
                

finally:
        GPIO.cleanup()