from django.db import models

# Create your models here.

class Modelo_equipamento(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    fabricante = models.CharField(max_length=100)
    data_criacao = models.DateTimeField(auto_now_add=True)
    descricao = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nome