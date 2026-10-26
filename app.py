import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522

leitorRFID = SimpleMFRC522()

try:
        print("Aproxime o cartão do leitor...")
        id, texto = leitorRFID.read()
        print(f"ID: {id}")
        print(f"Texto: {texto}")

finally:
        GPIO.cleanup()