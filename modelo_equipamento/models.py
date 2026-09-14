from django.db import models

# Create your models here.

class Modelo_equipamento(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    fabricante = models.CharField(max_length=100)
    data_criacao = models.DateTimeField(auto_now_add=True)
    descricao = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nome

    @property
    def nome_sem_fabricante(self):
        """Nome sem o fabricante repetido ("SW HUAWEI S6730" → "SW S6730"), para
        o card de acesso, que já mostra o fabricante na cor da marca. Vazio
        quando o nome é só o fabricante ("PROXMOX")."""
        import re
        fabricante = (self.fabricante or '').strip()
        if not fabricante:
            return self.nome
        limpo = re.sub(rf'(?<!\w){re.escape(fabricante)}(?!\w)', '', self.nome, flags=re.IGNORECASE)
        return re.sub(r'\s{2,}', ' ', limpo).strip(' -')
