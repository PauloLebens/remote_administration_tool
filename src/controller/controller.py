import socket
import struct
import json
import uuid
from datetime import datetime, timezone

def send_message(sock, action, args=None):
    """
    Constrói o envelope JSON da mensagem, aplica o length-prefixing de 4 bytes
    e envia para o agente através do socket TCP.
    """
    if args is None:
        args = {}
        
    message = {
        "id"        : str(uuid.uuid4()),
        "tipo"      : "comando",
        "acao"      : action,
        "args"      : args,
        "timestamp" : datetime.now(timezone.utc).isoformat()
    }
    
    # Serializar para JSON e codificar em UTF-8
    body_bytes = json.dumps(message).encode('utf-8')
    
    # Criar cabeçalho de 4 bytes com o tamanho exato do corpo (Big-Endian '!I')
    header = struct.pack('!I', len(body_bytes))
    
    # Enviar cabeçalho + corpo
    sock.sendall(header + body_bytes)
    print(f"[Enviado] Ação: {action} | ID: {message['id']}")

def recv_all(sock, n):
    """Garante a leitura exata de n bytes do stream TCP."""
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return bytes(data)

def run_controller(host='127.0.0.1', port=5000):
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    print(f"[*] A ligar ao agente em {host}:{port}...")
    try:
        client_socket.connect((host, port))
        print("[+] Ligação estabelecida com sucesso!")
        
        # Exemplo de teste: Enviar um comando simulado
        send_message(client_socket, "EXEC", {"cmd": "echo 'Ola do Controlador!'"})
        
        # Ler a resposta do agente com o mesmo mecanismo de framing
        raw_length = recv_all(client_socket, 4)
        if raw_length:
            msg_length = struct.unpack('!I', raw_length)[0]
            raw_data = recv_all(client_socket, msg_length)
            response = json.loads(raw_data.decode('utf-8'))
            
            print(f"\n[Resposta Recebida do Agente]:")
            print(json.dumps(response, indent=4, ensure_ascii=False))
            
    except ConnectionRefusedError:
        print("[-] Erro: Não foi possível ligar ao agente. Certifique-se de que ele está a correr.")
    finally:
        client_socket.close()
        print("[-] Ligação encerrada.")

if __name__ == "__main__":
    run_controller()