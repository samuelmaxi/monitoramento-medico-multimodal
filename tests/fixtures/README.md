# Fixtures de teste

Coloque aqui um arquivo de áudio curto (poucos segundos) com fala em português
(pt-BR), nomeado `sample_pt_br.wav` (ou defina outro caminho via a variável de
ambiente `SMOKE_TEST_AUDIO_PATH`), para que `tests/test_smoke.py` consiga testar o
fluxo de transcrição contra o Amazon Transcribe real.

Esse arquivo não é commitado (veja `.gitignore`) — se estiver ausente, o teste de
transcrição é pulado automaticamente com uma mensagem explicando este passo.
