# videoludique.ca — version statique (GitHub Pages)

Migration du blogue **VIDÉOLUDIQUE.CA** de WordPress vers un site **statique**
hébergé sur **GitHub Pages**, en conservant exactement les mêmes URLs.

- Structure des articles : `/AAAA/MM/JJ/slug/` (identique à WordPress)
- Page : `/a-propos/`
- Archives de catégories : `/category/<slug>/`
- Archives d’étiquettes : `/tag/<slug>/`
- Archives par date : `/AAAA/`, `/AAAA/MM/`
- Flux RSS : `/feed/index.xml` · Plan du site : `/sitemap.xml`
- Design inspiré du thème d’origine (Masu) : bourgogne `#501511` sur crème
  `#fff8ee`, police *Libre Franklin*.

## Comment ça marche

Le site est **généré** à partir du contenu de WordPress. Deux scripts, aucune
dépendance à installer (Python 3 standard) :

| Fichier | Rôle |
|---|---|
| `fetch_content.py` | Télécharge tout le contenu (articles, pages, catégories, étiquettes, médias) depuis l’API REST de WordPress dans `_source/`. |
| `build.py` | Génère le site statique (HTML) à la racine du dépôt, à partir de `_source/`. |
| `scripts/download_media.sh` | Télécharge les images dans `wp-content/uploads/…`. |
| `_source/` | Données sources (JSON + fragments HTML des articles). |
| `assets/style.css` | Feuille de style du site. |

### Régénérer / compléter le site

> ⚠️ Le contenu a été amorcé depuis un environnement **sans accès réseau** :
> une partie des articles est déjà présente dans `_source/posts/`, mais pour
> obtenir **la totalité des 181 articles et toutes les images**, il faut lancer
> `fetch_content.py` depuis une machine connectée à Internet (ou laisser
> l’action GitHub le faire, voir plus bas).

```bash
python3 fetch_content.py           # récupère TOUT le contenu depuis WordPress
python3 build.py                   # génère le site statique
bash scripts/download_media.sh     # télécharge les images localement
git add -A && git commit -m "Régénère le site" && git push
```

## Déploiement sur GitHub Pages

### Option A — Automatique (recommandé)

Le dépôt contient une action GitHub (`.github/workflows/deploy.yml`) qui, à
chaque *push* (et une fois par semaine), **récupère le contenu, génère le site,
télécharge les images et déploie** sur GitHub Pages.

1. Dans **Settings → Pages**, mettre *Source* = **GitHub Actions**.
2. Aller dans l’onglet **Actions** et lancer le workflow *« Build & deploy to
   GitHub Pages »* (ou faire un *push*).
3. Le site se construit **complet** (tous les articles + images) et se déploie.

### Option B — Servir directement les fichiers

*Settings → Pages → Source = Deploy from a branch*, dossier `/` (racine). Le
fichier `.nojekyll` est présent pour désactiver Jekyll.

## Domaine personnalisé

Le fichier `CNAME` contient déjà `videoludique.ca`. Pour la bascule DNS,
faire pointer le domaine vers GitHub Pages :

- Enregistrements **A** de l’apex `videoludique.ca` vers :
  `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`
- Enregistrement **CNAME** `www` → `<votre-utilisateur>.github.io`

Comme toutes les URLs internes et toutes les images utilisent des **chemins
absolus** (`/2026/06/06/...`, `/wp-content/uploads/...`), rien ne casse après la
bascule : les liens et images restent valides sur le nouveau hébergement.

## Notes

- Les intégrations YouTube / VideoPress sont conservées (iframes).
- Les images sont servies depuis `/wp-content/uploads/…` — mêmes chemins que
  WordPress. Tant que `download_media.sh` n’a pas été exécuté, les balises
  `<img>` pointent vers ces chemins mais les fichiers ne sont pas encore dans le
  dépôt ; lancez le script (ou l’action) pour les rapatrier.
- Auteur / contact : Mario J. Ramos — info@mariojramos.com
