# Кто ведёт обращение. Обе колонки добавляются пустыми и без индекса, поэтому на
# живой PostgreSQL 16 это ALTER без переписывания таблицы: уже открытые тикеты
# остаются ничьими до первого ответа оператора.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('telegram_bot', '0006_supportprompt_supportticket'),
    ]

    operations = [
        migrations.AddField(
            model_name='supportticket',
            name='operator_name',
            field=models.CharField(blank=True, max_length=64, verbose_name='Имя оператора'),
        ),
        migrations.AddField(
            model_name='supportticket',
            name='operator_telegram_id',
            field=models.BigIntegerField(blank=True, null=True, verbose_name='Оператор'),
        ),
    ]
