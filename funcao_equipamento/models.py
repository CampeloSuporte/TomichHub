from django.db import models

class Funcao_equipamento(models.Model):
    data_criacao = models.DateTimeField(auto_now_add=True)
    descricao = models.TextField(unique=True)

    def __str__(self):
        return self.descricao or "Sem descrição"