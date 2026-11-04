# Pipeline d'ingestion Data Factory

Le pipeline `pl_maintenance` télécharge les 5 CSV depuis GitHub vers OneLake, puis enchaîne les 3 notebooks.

```
Pour chaque fichier ──► 01_bronze ──► 02_silver ──► 03_gold
 (copie vers Files/brut)
```

- **Source :** les CSV bruts d'un dépôt GitHub public, lus en HTTP anonyme. C'est gratuit et sans clé à gérer, et les données sont fictives.
- **Recharge complète :** chaque exécution remplace les fichiers et les tables. Le test local confirme qu'une deuxième exécution donne les mêmes résultats.
- **Arrêt en cas de problème :** si une copie échoue, les notebooks ne démarrent pas. Si un fichier est vide ou incomplet, `01_bronze` échoue avant d'écrire, et le rapport garde les données de la dernière exécution réussie.

Le pipeline se construit dans l'interface de Fabric plutôt qu'à partir d'un fichier JSON : Microsoft ne publie pas de schéma officiel pour ce format, et des utilisateurs signalent qu'il change. Ce guide n'a pas encore été suivi dans Fabric : les libellés de l'interface peuvent différer légèrement.

**Prérequis :** le lakehouse existe, et les 3 notebooks sont importés et attachés à ce lakehouse comme lakehouse par défaut.

## 1. Vérifier la source

Les CSV sont publiés dans le dépôt public [snappyyyy007/maintenance-hospitaliere](https://github.com/snappyyyy007/maintenance-hospitaliere). Vérifie qu'un fichier répond :

```bash
curl -I https://raw.githubusercontent.com/snappyyyy007/maintenance-hospitaliere/main/data/brut/priorites.csv
```

## 2. Créer le pipeline et sa connexion

Dans ton espace de travail, crée un pipeline nommé `pl_maintenance`. Dans l'onglet **Parameters**, ajoute :

| Nom | Type | Valeur par défaut |
|---|---|---|
| `chemin_source` | String | `snappyyyy007/maintenance-hospitaliere/main/data/brut/` |
| `fichiers` | Array | `["bons_de_travail","locaux","pavillons","techniciens","priorites"]` |

La barre oblique finale de `chemin_source` compte, car le nom du fichier y est collé directement.

## 3. Copier les fichiers

Ajoute une activité **ForEach** nommée `Pour chaque fichier` :

| Réglage | Valeur |
|---|---|
| Sequential | Décoché |
| Items | `@pipeline().parameters.fichiers` |

Dans cette boucle, ajoute une activité **Copy data** nommée `Copier le CSV`.

**Onglet Source**

| Réglage | Valeur |
|---|---|
| Connection | Nouvelle connexion **HTTP** |
| Base URL | `https://raw.githubusercontent.com/` |
| Authentication | Anonymous |
| Relative URL | `@concat(pipeline().parameters.chemin_source, item(), '.csv')` |
| Request method | GET |
| File format | Binary |

**Onglet Destination**

| Réglage | Valeur |
|---|---|
| Connection | Ton lakehouse |
| Root folder | Files |
| File path, dossier | `brut` |
| File path, fichier | `@concat(item(), '.csv')` |
| File format | Binary |

Le format Binary copie le fichier tel quel. C'est ce qu'il faut ici : la couche bronze doit recevoir les défauts intacts.

## 4. Enchaîner les notebooks

Ajoute 3 activités **Notebook** et relie-les par leur sortie **On success** (la coche verte) :

| Activité | Notebook | Démarre après |
|---|---|---|
| `Bronze` | `01_bronze` | `Pour chaque fichier` |
| `Silver` | `02_silver` | `Bronze` |
| `Gold` | `03_gold` | `Silver` |

Aucun paramètre à passer : les valeurs par défaut des notebooks conviennent.

## 5. Tester

1. Clique sur **Validate**, puis sur **Run**.
2. Suis l'exécution dans l'onglet **Output**. Les 8 activités (5 copies, 3 notebooks) doivent réussir.
3. Vérifie les résultats dans le point de terminaison SQL du lakehouse :

| Table | Lignes attendues |
|---|---:|
| `bronze_bons_de_travail` | 8 696 |
| `silver_bons_de_travail` | 8 565 |
| `silver_quarantaine` | 62 |
| `gold_fait_bons` | 8 565 |

Pour prouver l'arrêt en cas de problème, ce qui fait une bonne séquence de démo, pousse sur GitHub un `priorites.csv` vide et relance. `Bronze` doit échouer, et `Silver` et `Gold` ne doivent pas démarrer. Rétablis ensuite le fichier avec `git revert`.

## 6. Planifier

Dans **Schedule**, active une exécution hebdomadaire, avec le fuseau de Montréal et une date de fin proche.

Les CSV ne changent pas d'eux-mêmes : la planification démontre l'automatisation, sans apporter de nouvelles données. Chaque exécution consomme de la capacité Fabric, alors désactive-la après ta démo. Si tu arrêtes la capacité, les exécutions planifiées ne passent plus.

## En cas d'erreur

| Symptôme | Cause probable |
|---|---|
| 404 sur la copie | Dépôt privé, ou `chemin_source` erroné |
| Ancien fichier copié juste après un `git push` | GitHub garde les fichiers bruts en cache 5 minutes |
| Notebook en échec au premier accès à `Files/brut` | Aucun lakehouse par défaut attaché au notebook |
| `Bronze` en échec avec « colonnes manquantes » ou « aucune ligne » | Le contrôle a fonctionné : un CSV source est défectueux |

## Pour aller plus loin

Le bonus de migration vers Azure Database for PostgreSQL ne change que l'étape 3 : la source devient une table PostgreSQL, et le reste de la chaîne ne bouge pas si les CSV gardent les mêmes noms et colonnes.
