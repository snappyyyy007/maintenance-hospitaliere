# Analyse de la maintenance hospitalière

Plateforme de données de bout en bout sur Microsoft Fabric pour suivre les bons de travail d'un hôpital : délais d'intervention, coûts par pavillon et problèmes récurrents.

> **Toutes les données sont fictives.** L'établissement, les pavillons, les locaux et les techniciens sont inventés. Aucune donnée ne provient d'un hôpital réel.

## État du projet

| Étape | Contenu | État |
|---|---|---|
| 1. Source | Jeu de données fictif en CSV | Fait |
| 1b. Bonus | Migration vers Azure Database for PostgreSQL | À faire |
| 2. Ingestion | Pipeline Data Factory vers OneLake | Préparé, à construire dans Fabric ([guide](fabric/pipeline.md)) |
| 3. Transformation | Notebooks PySpark bronze, silver, gold | Testé en local, à importer dans Fabric |
| 4. Rapport | Modèle sémantique, mesures DAX, Power BI | Préparé, à construire dans Fabric ([guide](powerbi/modele_semantique.md)) |
| 5. Bonus | Capteurs de fuite simulés, KQL, alerte | À faire |
| 6. Bonus | Agent Azure AI Foundry | À faire |

## Générer et valider les données

Python 3.9 ou plus récent, sans dépendance externe.

```bash
python3 scripts/generer_donnees.py
```

```bash
python3 scripts/valider_donnees.py
```

La graine est fixe (`--graine 42` par défaut) : deux exécutions produisent exactement le même fichier. Le script de validation échoue si une tendance ou un défaut annoncé ci-dessous est absent.

## Exécuter les notebooks dans Fabric

| Notebook | Rôle | Tables créées |
|---|---|---|
| `notebooks/01_bronze.ipynb` | Copie les CSV sans rien corriger | `bronze_*` |
| `notebooks/02_silver.ipynb` | Nettoie, type et isole les bons invalides | `silver_*`, dont `silver_quarantaine` |
| `notebooks/03_gold.ipynb` | Construit le modèle en étoile | `gold_fait_bons`, `gold_dim_*`, `gold_agg_mensuelle`, `gold_points_chauds` |

1. Crée un lakehouse dans ton espace de travail Fabric.
2. Importe les 3 notebooks et attache-les à ce lakehouse comme lakehouse par défaut.
3. Construis le pipeline du [guide](fabric/pipeline.md), qui copie les CSV et enchaîne les notebooks. Pour un essai rapide sans pipeline, téléverse les 5 CSV de `data/brut/` dans `Files/brut` et exécute les notebooks dans l'ordre.

Chaque notebook échoue si un contrôle ne passe pas : un fichier source vide ou incomplet ne remplace donc pas les tables existantes.

## Tester les notebooks en local

Il faut PySpark 3.5 et Java 17, avec `JAVA_HOME` défini.

```bash
python scripts/tester_notebooks.py
```

Le script exécute la chaîne deux fois, comme deux exécutions planifiées, et vérifie qu'un fichier défectueux l'arrête. Il compare ensuite les tables à un calcul indépendant en Python pur et au manifeste. Il écrit en Parquet plutôt qu'en Delta : l'écriture Delta et l'exécution dans Fabric ne sont donc pas couvertes par ce test.

### Résultats de la chaîne

| Étape | Bons |
|---|---:|
| Lignes brutes (bronze) | 8 696 |
| Après retrait des doublons | 8 627 |
| En quarantaine | 62 |
| Bons valides (silver et gold) | 8 565 |

Le coût total des bons valides fermés est de 5 368 405,16 $.

## Modèle sémantique et rapport

Le dossier `powerbi/` se suit dans le navigateur, sans Power BI Desktop :

| Fichier | Contenu |
|---|---|
| `modele_semantique.md` | Tables, relations, formats et les 4 pages du rapport |
| `mesures.dax` | 22 mesures et les requêtes pour les vérifier |
| `valeurs_attendues.csv` | Résultat attendu de chaque mesure, calculé en SQL par `tester_notebooks.py` |

Le DAX n'a pas encore été exécuté : le test local contrôle ses références, pas ses résultats.

## Le jeu de données

Il couvre du 1er octobre 2023 au 30 septembre 2026, avec 8 627 bons uniques, soit 8 696 lignes brutes avec les doublons.

| Fichier (`data/brut/`) | Contenu | Lignes |
|---|---|---|
| `bons_de_travail.csv` | Table de faits, volontairement imparfaite | 8 696 |
| `locaux.csv` | Local, étage, type, criticité (1 à 3) | 664 |
| `pavillons.csv` | Nom, vocation, année, superficie, étages | 7 |
| `techniciens.csv` | Métier, quart, interne ou sous-traitant, taux horaire | 26 |
| `priorites.csv` | Libellé et cible de fermeture en heures | 4 |

`data/manifeste.json` donne le nombre exact de chaque défaut injecté et la liste des points chauds. Il sert de corrigé pour vérifier la couche silver : ne le charge pas dans le lakehouse.

### Colonnes de `bons_de_travail.csv`

| Colonne | Description |
|---|---|
| `bon_id` | Identifiant, `BT-AAAA-NNNNNN` |
| `bon_parent_id` | Dégât d'eau à l'origine du bon, s'il y a lieu |
| `date_creation`, `date_assignation`, `date_debut_travaux`, `date_fermeture` | Jalons du bon, vides s'ils n'ont pas encore eu lieu |
| `statut` | Ouvert, Assigné, En cours, Fermé, Annulé |
| `type_bon` | Correctif ou Préventif |
| `priorite` | P1 à P4 |
| `categorie`, `sous_categorie` | Plomberie, Électricité, CVC, Dégât d'eau, puis le problème précis |
| `pavillon_id`, `etage`, `local_id` | Emplacement |
| `source` | Portail web, Téléphone, Système GTB, Planifié |
| `description` | Texte libre court |
| `technicien_id` | Technicien assigné |
| `heures_main_oeuvre` | Heures facturées |
| `cout_main_oeuvre`, `cout_pieces`, `cout_sous_traitance` | Coûts en dollars canadiens ; le coût total est à calculer en silver |

### Défauts à corriger en silver

| Défaut | Nombre | Traitement suggéré |
|---|---:|---|
| Doublons exacts | 69 | Dédoublonner |
| Catégories mal écrites (`HVAC`, `ELEC`, `DEGAT EAU`) | 517 | Table de correspondance |
| Codes de pavillon mal écrits (`pav-b`, `PAV B`) | 129 | Normaliser |
| Dates au format `JJ/MM/AAAA HH:MM` | 431 | Lire les deux formats |
| Fermetures avant la création | 33 | Isoler en quarantaine |
| Coûts de pièces négatifs | 21 | Isoler en quarantaine |
| Coûts de pièces manquants | 168 | Garder vide et signaler |
| Technicien manquant sur un bon fermé | 126 | Remplacer par « Inconnu » |
| Heures aberrantes (999) | 8 | Isoler en quarantaine |

### Tendances à retrouver dans le rapport

Ces résultats sortent de `valider_donnees.py`, après un nettoyage minimal, sauf le taux de récidive, qui vient de `gold_fait_bons`. Ils diffèrent donc légèrement des chiffres du rapport, qui excluent aussi la quarantaine ; la référence pour Power BI est `powerbi/valeurs_attendues.csv`.

- **Vétusté :** le pavillon Héritage (1958) compte 36,1 dégâts d'eau par 10 000 m², contre 7,9 pour le pavillon des Érables (2014).
- **Saisons :** les bons CVC montent l'été (396 en juillet, 179 en avril) et les dégâts d'eau au dégel (67 en avril, 27 en septembre).
- **Coûts :** un dégât d'eau coûte environ 6 700 $ en moyenne, contre 270 $ à 385 $ pour les autres catégories.
- **Récidive :** 15,0 % des bons correctifs reviennent dans le même local et la même catégorie en 30 jours ou moins (1 122 sur 7 488). Les 14 points chauds cachés ressortent dans `gold_points_chauds`, parmi 71 couples local-catégorie qui dépassent le seuil de 4 récidives : le flux normal se concentre aussi dans certains locaux des vieux pavillons.
- **Effet domino :** un dégât d'eau crée souvent des bons liés, comme la recherche de la cause à l'étage du dessus ou une vérification électrique.

| Priorité | Cible de fermeture | Médiane observée | Respect de la cible |
|---|---:|---:|---:|
| P1 Urgence | 12 h | 9,3 h | 61 % |
| P2 Élevée | 48 h | 17,2 h | 89 % |
| P3 Normale | 120 h | 59,8 h | 82 % |
| P4 Planifiée | 720 h | 322,6 h | 89 % |

### Définition de la récidive

Un bon est une récidive s'il est correctif, non annulé, et créé au plus 30 jours après le bon précédent du même local et de la même catégorie. Garde cette définition identique dans le notebook gold et la mesure DAX, pour que les chiffres concordent.
