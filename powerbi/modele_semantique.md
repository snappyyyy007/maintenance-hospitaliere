# Modèle sémantique et rapport Power BI

Ce guide se suit entièrement dans le navigateur, depuis Fabric. Power BI Desktop n'est pas nécessaire, ce qui compte sur Mac, où il n'est pas offert.

**Prérequis :** les 3 notebooks ont tourné dans le lakehouse. Les tables doivent être en Delta (le format par défaut des notebooks), car le mode Direct Lake lit uniquement ce format.

## 1. Créer le modèle

1. Ouvre le lakehouse et choisis **New semantic model**.
2. Nomme-le `Maintenance hospitalière` et sélectionne ces 8 tables :
   - `gold_fait_bons`
   - `gold_dim_date`, `gold_dim_pavillon`, `gold_dim_local`, `gold_dim_technicien`, `gold_dim_priorite`
   - `gold_points_chauds`
   - `silver_quarantaine`
3. Ouvre le modèle avec **Open data model**, puis passe en mode édition en haut à droite.

`gold_agg_mensuelle` reste hors du modèle : elle sert à l'agent IA de l'étape 6, et les mesures recalculent déjà ces chiffres.

## 2. Créer les relations

Toutes les relations sont de type plusieurs à un, avec un filtrage à sens unique. L'éditeur web ne valide pas la cardinalité : vérifie-la toi-même.

| De (plusieurs) | Vers (un) | État |
|---|---|---|
| `gold_fait_bons[date_creation_jour]` | `gold_dim_date[date]` | Active |
| `gold_fait_bons[date_fermeture_jour]` | `gold_dim_date[date]` | **Inactive** |
| `gold_fait_bons[pavillon_id]` | `gold_dim_pavillon[pavillon_id]` | Active |
| `gold_fait_bons[local_id]` | `gold_dim_local[local_id]` | Active |
| `gold_fait_bons[technicien_id]` | `gold_dim_technicien[technicien_id]` | Active |
| `gold_fait_bons[priorite]` | `gold_dim_priorite[priorite]` | Active |
| `gold_points_chauds[pavillon_id]` | `gold_dim_pavillon[pavillon_id]` | Active |
| `gold_points_chauds[local_id]` | `gold_dim_local[local_id]` | Active |

- **Pas de relation entre `gold_dim_local` et `gold_dim_pavillon`.** Elle créerait deux chemins entre les bons et le pavillon, ce que le modèle refuse.
- **`silver_quarantaine` reste isolée.** Elle ne sert qu'à afficher le nombre de bons exclus.

Marque ensuite `gold_dim_date` comme table de dates, avec `date` comme colonne. Trie aussi `mois_nom` par `mois_numero` et `jour_semaine_nom` par `jour_semaine_numero`, sinon les mois s'affichent en ordre alphabétique.

Masque les colonnes techniques de `gold_fait_bons` : les clés, `est_*` et `date_creation`.

## 3. Ajouter les mesures

1. Ouvre la **vue de requête DAX** et colle tout le contenu de `mesures.dax`.
2. Exécute la requête. Elle renvoie 3 tableaux, à comparer à `valeurs_attendues.csv`.
3. Si tout concorde, clique sur **Update model with changes**.

Si la vue de requête DAX n'apparaît pas, vérifie que le paramètre de l'espace de travail qui permet de modifier les modèles dans le service Power BI est activé.

Le fichier DAX ne fixe pas les formats. Règle-les dans le volet des propriétés :

| Mesures | Format |
|---|---|
| Coûts | `#,0 $` |
| `Coût par m²`, `Coût moyen par bon fermé` | `#,0.00 $` |
| Taux, parts et `Variation annuelle des bons` | `0.0 %` |
| Délais | `#,0.0` |
| Nombres de bons | `#,0` |

### Vérification

Le DAX n'a pas été exécuté pendant le développement : seules ses références ont été contrôlées. Cette comparaison est donc son premier vrai test. Un écart sur un chiffre pointe vers la mesure ou la relation concernée, car `valeurs_attendues.csv` vient d'un calcul SQL séparé sur les mêmes tables gold.

| Contexte | Mesure | Valeur attendue |
|---|---|---:|
| Global | Nb bons | 8 397 |
| Global | Coût total | 5 368 405,16 $ |
| Global | Taux de récidive | 14,98 % |
| PAV-B | Dégâts d'eau par 10 000 m² | 35,48 |
| Année 2026 | Nb bons N-1 | 2 234 |

La mesure `Nb bons N-1` est la plus à risque, car elle passe une table filtrée à `SAMEPERIODLASTYEAR`. Vérifie-la en priorité.

## 4. Construire le rapport

Crée un rapport à partir du modèle. Mets les mêmes segments sur chaque page : année, trimestre, pavillon et catégorie. Garde une couleur fixe par catégorie dans tout le rapport.

### Page 1 · Vue d'ensemble

**Message :** les dégâts d'eau représentent 5 % des bons, mais la moitié des coûts.

| Visuel | Champs |
|---|---|
| Cartes | `Nb bons`, `Coût total`, `Délai médian de fermeture (h)`, `Taux de respect de la cible`, `Taux de récidive`, `Nb bons ouverts` |
| Courbe | `Nb bons` par `annee_mois` et `categorie` |
| Barres | `Coût total` par `categorie` |
| Barres | `Nb bons` par `categorie` |
| Carte discrète | `Nb bons en quarantaine`, avec le libellé « exclus de l'analyse » |

### Page 2 · Délais

**Message :** les P1 respectent leur cible le moins souvent.

| Visuel | Champs |
|---|---|
| Tableau | `priorite`, `libelle`, `cible_fermeture_heures`, `Délai médian de fermeture (h)`, `Taux de respect de la cible` |
| Courbe | `Délai médian de fermeture (h)` par `annee_mois` |
| Barres | `Taux de respect de la cible` par pavillon |
| Courbes | `Nb bons` et `Bons fermés par date de fermeture` par `annee_mois` |

### Page 3 · Coûts par pavillon

**Message :** à superficie égale, le pavillon Héritage coûte 4 fois plus que le pavillon Boréal (45,87 $ contre 11,08 $ par m²).

| Visuel | Champs |
|---|---|
| Barres triées | `Coût par m²` par `nom` du pavillon |
| Barres empilées | `Coût total` par pavillon et `categorie` |
| Nuage de points | `annee_construction` et `Coût par m²`, un point par pavillon |
| Courbe | `Coût cumulé annuel` par mois, une ligne par année |

### Page 4 · Récidives et dégâts d'eau

**Message :** les problèmes reviennent dans peu de locaux, et l'eau frappe au dégel.

| Visuel | Champs |
|---|---|
| Barres | `Taux de récidive` par pavillon |
| Tableau | `gold_points_chauds`, trié par `nb_recidives` : local, pavillon, `type_local`, `categorie`, `nb_bons`, `nb_recidives`, `cout_total` |
| Colonnes | `Nb dégâts d'eau` par `mois_nom` |
| Barres | `Dégâts d'eau par 10 000 m²` par pavillon |

`gold_points_chauds` couvre les 3 années entières. Les segments d'année, de trimestre et de catégorie ne filtrent pas ce tableau : indique-le dans son titre, ou désactive ces interactions.

## Limites à connaître

- **Coûts :** ils sont rattachés à la date de création du bon, pas à la date de la dépense.
- **Bons exclus :** les 62 bons en quarantaine ne figurent dans aucun chiffre.
- **Années partielles :** 2023 commence en octobre et 2026 finit en septembre. `Nb bons N-1` compare 2026 à la même période de 2025, mais les totaux annuels bruts ne sont pas comparables.
- **Coûts manquants :** 166 bons fermés n'ont pas de coût de pièces et comptent pour 0 $ à ce poste, ce qui sous-estime légèrement le total.
