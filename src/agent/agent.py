import socket
import struct
import json

def recv_all(sock, n):
    """
    Garante que lê exatamente n bytes do socket, contornando
    o comportamento de stream do TCP (fragmentação).
    """
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return bytes(data)

def run_agent(host='0.0.0.0', port=5000):
    # Criação do socket TCP (família IPv4, tipo TCP/SOCK_STREAM)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # Permite reutilizar a porta imediatamente após fechar o programa
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    server_socket.bind((host, port))
    server_socket.listen(1)
    print(f"[*] Agente (Servidor) a escutar na porta {port}...")

    while True:
        client_sock, addr = server_socket.accept()
        print(f"[+] Conexão aceite do controlador: {addr}")
        
        try:
            while True:
                # 1. Ler o cabeçalho de 4 bytes com o tamanho da mensagem (Big-Endian '!I')
                raw_length = recv_all(client_sock, 4)
                if not raw_length:
                    break
                
                msg_length = struct.unpack('!I', raw_length)[0]
                
                # 2. Ler exatamente o corpo da mensagem com base no tamanho N
                raw_data = recv_all(client_sock, msg_length)
                if not raw_data:
                    break
                
                # 3. Decodificar o JSON recebido
                message = json.loads(raw_data.decode('utf-8'))
                print(f"[Recebido] ID: {message.get('id')} | Ação: {message.get('acao')}")
                
                # 4. Construir a resposta de eco (para testar o protocolo)
                response = {
                    "id": message.get("id"),
                    "tipo": "resposta",
                    "acao": message.get("acao"),
                    "status": "ok",
                    "resultado": "Comando processado com sucesso pelo agente."
                }
                
                # 5. Empacotar e enviar a resposta de volta com o mesmo framing de 4 bytes
                resp_bytes = json.dumps(response).encode('utf-8')
                resp_header = struct.pack('!I', len(resp_bytes))
                
                client_sock.sendall(resp_header + resp_bytes)

        except (ConnectionResetError, BrokenPipeError):
            print("[-] Conexão interrompida abruptamente pelo controlador.")
        finally:
            client_sock.close()
            print("[-] Conexão fechada. À espera de nova ligação...\n")

if __name__ == "__main__":
    run_agent()